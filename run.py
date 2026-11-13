import atexit
import os
import subprocess
import sys

from app import create_app

app = create_app()

for folder in (app.config['UPLOAD_FOLDER'], app.config['RESULTS_FOLDER'], app.instance_path):
    os.makedirs(folder, exist_ok=True)


def start_comparison_worker():
    """Keep one executor in the reloader parent, never on Gunicorn import."""
    worker = subprocess.Popen([sys.executable, '-m', 'app.comparison.worker'])

    def stop_worker():
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()

    atexit.register(stop_worker)


if __name__ == '__main__':
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        start_comparison_worker()
    app.run(debug=True)
