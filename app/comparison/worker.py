"""A single queue consumer with bounded, isolated algorithm processes."""
import json
import logging
import multiprocessing as mp
import os
import signal
import shutil
import threading
import time
import uuid

# Set numerical-library limits before numpy/scipy are imported in the worker
# and its spawned children, which avoids transient thread oversubscription.
for variable in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
                 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS'):
    os.environ[variable] = '1'

import pandas as pd
from threadpoolctl import threadpool_limits

from app import create_app
from app.comparison import store
from app.comparison.execution import data_error, execute_task
from app.core.data_processor import process_data_for_reduction

logger = logging.getLogger(__name__)


def run_job(app, path, job, stop):
    job_id = job['id']
    active = {}
    try:
        dataset = os.path.join(app.config['UPLOAD_FOLDER'], job['dataset'])
        df = pd.read_csv(dataset) if dataset.lower().endswith('.csv') else pd.read_excel(dataset)
        common = json.loads(job['params'])
        classes = df[common['target_column']].nunique()
        with threadpool_limits(limits=1):
            X, y = process_data_for_reduction(df, common)
        with store.connection(path) as db:
            tasks = {row['algorithm']: dict(row) for row in db.execute('SELECT * FROM tasks WHERE job_id=?', (job_id,))}
        waiting = []
        for name in store.ALGORITHMS:
            task = tasks[name]
            if task['status'] != 'queued':
                continue
            params = json.loads(task['params'])
            error = data_error(name, params, X, y, classes)
            if error:
                store.update_task(path, job_id, name, 'failed', error=error)
            else:
                waiting.append((name, params))

        context = mp.get_context('spawn')
        limit = max(1, min(int(app.config['COMPARISON_WORKERS']), os.cpu_count() or 1))
        timeout = max(1, int(app.config['COMPARISON_TIMEOUT']))
        while (waiting or active) and not stop.is_set():
            while waiting and len(active) < limit and not stop.is_set():
                name, params = waiting.pop(0)
                receiver, sender = context.Pipe(duplex=False)
                process = context.Process(target=execute_task, args=(name, params, X, y, app.config['RESULTS_FOLDER'], sender))
                store.update_task(path, job_id, name, 'running')
                try:
                    process.start()
                except Exception:
                    logger.exception('Could not start %s for comparison %s', name, job_id)
                    sender.close()
                    receiver.close()
                    store.update_task(path, job_id, name, 'failed', error='execution_error')
                    continue
                sender.close()
                active[name] = (process, receiver, time.monotonic())
            for name, (process, receiver, started) in list(active.items()):
                if receiver.poll():
                    try:
                        result = receiver.recv()
                    except EOFError:
                        result = {'error': 'execution_error'}
                    process.join(timeout=1)
                    if process.is_alive():
                        process.terminate()
                        process.join()
                    if 'error' in result:
                        logger.warning('%s failed for comparison %s: %s', name, job_id, result['error'])
                        store.update_task(path, job_id, name, 'failed', error='execution_error:' + str(result['error'])[:500])
                    else:
                        store.update_task(path, job_id, name, 'completed', metrics=result['metrics'], plot=result['plot'], csv=result['csv'])
                    receiver.close()
                    del active[name]
                elif not process.is_alive() or time.monotonic() - started > timeout:
                    timed_out = process.is_alive()
                    if timed_out:
                        process.terminate()
                    process.join()
                    receiver.close()
                    store.update_task(path, job_id, name, 'failed', error='timeout' if timed_out else 'execution_error')
                    del active[name]
            stop.wait(0.1)
        if stop.is_set():
            store.finish_job(path, job_id, error='interrupted')
        else:
            store.finish_job(path, job_id)
    except Exception:
        logger.exception('Could not process comparison %s', job_id)
        store.finish_job(path, job_id, error='preprocessing_error')
    finally:
        for process, receiver, _ in active.values():
            if process.is_alive():
                process.terminate()
            process.join()
            receiver.close()


def main():
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    for folder in (app.instance_path, app.config['UPLOAD_FOLDER'], app.config['RESULTS_FOLDER']):
        os.makedirs(folder, exist_ok=True)
    path = store.database_path(app)
    token = uuid.uuid4().hex
    if not store.acquire_executor(path, token):
        logger.error('A comparison executor is already running.')
        return 1
    # Remove only temporary directories created by an interrupted executor.
    for entry in os.scandir(app.config['RESULTS_FOLDER']):
        if entry.name.startswith('.comparison-') and entry.is_dir(follow_symlinks=False):
            if os.path.isfile(os.path.join(entry.path, '.reduzlab-temporary')):
                shutil.rmtree(entry.path)
    stop = threading.Event()

    def shutdown(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    def keep_alive():
        while not stop.wait(5):
            try:
                if not store.heartbeat(path, token):
                    stop.set()
            except Exception:
                logger.exception('Could not update executor heartbeat')

    thread = threading.Thread(target=keep_alive, daemon=True)
    thread.start()
    try:
        while not stop.is_set():
            job = store.claim_next(path)
            if job:
                run_job(app, path, job, stop)
            else:
                stop.wait(0.5)
    finally:
        stop.set()
        thread.join(timeout=6)
        store.release_executor(path, token)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
