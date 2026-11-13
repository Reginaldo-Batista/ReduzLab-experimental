"""SQLite-backed queue shared by the web server and its single executor."""
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

ALGORITHMS = ('PCA', 'KPCA', 'T-SNE', 'LDA', 'NCA', 'LLE', 'Isomap')
ACTIVE = ('queued', 'running')
LEASE_SECONDS = 30


def database_path(app):
    return os.path.join(app.instance_path, 'comparisons.sqlite3')


@contextmanager
def connection(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('PRAGMA busy_timeout = 30000')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, dataset TEXT NOT NULL,
                params TEXT NOT NULL, locale TEXT NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, error TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                algorithm TEXT NOT NULL, status TEXT NOT NULL, params TEXT NOT NULL,
                error TEXT, metrics TEXT, plot TEXT, csv TEXT,
                PRIMARY KEY (job_id, algorithm)
            );
            CREATE TABLE IF NOT EXISTS executor (
                id INTEGER PRIMARY KEY CHECK (id = 1), token TEXT NOT NULL,
                heartbeat REAL NOT NULL
            );
        ''')
        yield db
    finally:
        db.close()


def is_online(db):
    row = db.execute('SELECT heartbeat FROM executor WHERE id = 1').fetchone()
    return bool(row and row['heartbeat'] > time.time() - LEASE_SECONDS)


def acquire_executor(path, token):
    with connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if is_online(db):
            db.rollback()
            return False
        db.execute("UPDATE tasks SET status = 'interrupted', error = 'interrupted' WHERE status IN ('queued', 'running') AND job_id IN (SELECT id FROM jobs WHERE status IN ('queued', 'running'))")
        db.execute("UPDATE jobs SET status = 'interrupted', error = 'interrupted' WHERE status IN ('queued', 'running')")
        db.execute('INSERT OR REPLACE INTO executor (id, token, heartbeat) VALUES (1, ?, ?)', (token, time.time()))
        db.commit()
        return True


def heartbeat(path, token):
    with connection(path) as db:
        cursor = db.execute('UPDATE executor SET heartbeat = ? WHERE id = 1 AND token = ?', (time.time(), token))
        db.commit()
        return cursor.rowcount == 1


def release_executor(path, token):
    with connection(path) as db:
        db.execute('DELETE FROM executor WHERE id = 1 AND token = ?', (token,))
        db.commit()


def enqueue(path, owner, dataset, params, task_params, errors, locale):
    job_id = uuid.uuid4().hex
    with connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if not is_online(db):
            db.rollback()
            return None
        db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, NULL)',
                   (job_id, owner, dataset, json.dumps(params), locale, 'queued', time.time()))
        for name in ALGORITHMS:
            error = errors.get(name)
            db.execute('INSERT INTO tasks (job_id, algorithm, status, params, error) VALUES (?, ?, ?, ?, ?)',
                       (job_id, name, 'failed' if error else 'queued', json.dumps(task_params.get(name, {})), error))
        db.commit()
    return job_id


def get_job(path, job_id, owner=None):
    with connection(path) as db:
        job = db.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        if not job or (owner is not None and job['owner'] != owner):
            return None
        if job['status'] in ACTIVE and not is_online(db):
            db.execute('BEGIN IMMEDIATE')
            if not is_online(db):
                db.execute("UPDATE tasks SET status='interrupted', error='interrupted' WHERE job_id=? AND status IN ('queued', 'running')", (job_id,))
                db.execute("UPDATE jobs SET status='interrupted', error='interrupted' WHERE id=? AND status IN ('queued', 'running')", (job_id,))
                db.commit()
            else:
                db.rollback()
            job = db.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        tasks = db.execute('SELECT * FROM tasks WHERE job_id = ?', (job_id,)).fetchall()
        return dict(job), {row['algorithm']: dict(row) for row in tasks}


def claim_next(path):
    with connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created, id LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (row['id'],))
        db.commit()
        return dict(row) if row else None


def update_task(path, job_id, name, status, error=None, metrics=None, plot=None, csv=None):
    with connection(path) as db:
        db.execute('UPDATE tasks SET status=?, error=?, metrics=?, plot=?, csv=? WHERE job_id=? AND algorithm=? AND status IN (?, ?)',
                   (status, error, json.dumps(metrics) if metrics is not None else None, plot, csv, job_id, name, *ACTIVE))
        db.commit()


def finish_job(path, job_id, error=None):
    with connection(path) as db:
        if error:
            task_status = 'interrupted' if error == 'interrupted' else 'failed'
            db.execute("UPDATE tasks SET status=?, error=? WHERE job_id=? AND status IN ('queued', 'running')", (task_status, error, job_id))
        states = [row['status'] for row in db.execute('SELECT status FROM tasks WHERE job_id=?', (job_id,))]
        status = 'interrupted' if error == 'interrupted' else ('completed' if all(s == 'completed' for s in states) else ('completed_with_errors' if 'completed' in states else 'failed'))
        db.execute("UPDATE jobs SET status=?, error=? WHERE id=? AND status IN ('queued', 'running')", (status, error, job_id))
        db.commit()


def clear_history(path, folders):
    """Serialize deletion with job creation; keep files used by active jobs."""
    with connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if not is_online(db):
            db.execute("UPDATE tasks SET status='interrupted', error='interrupted' WHERE status IN ('queued', 'running')")
            db.execute("UPDATE jobs SET status='interrupted', error='interrupted' WHERE status IN ('queued', 'running')")
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued', 'running') LIMIT 1").fetchone():
            db.rollback()
            return None
        deleted = failed = 0
        for folder in folders:
            os.makedirs(folder, exist_ok=True)
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                if filename == '.gitkeep' or not os.path.isfile(file_path):
                    continue
                try:
                    os.remove(file_path)
                    deleted += 1
                except OSError:
                    failed += 1
        db.execute('DELETE FROM jobs')
        db.commit()
        return deleted, failed
