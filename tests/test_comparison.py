import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from sklearn.datasets import load_iris

from app import create_app
from app.algorithms.metrics import clustering_metrics
from app.comparison import store
from app.comparison.execution import data_error
from app.comparison.worker import run_job


def fake_execute_task(name, params, X, y, folder, channel):
    # The first result is ready while the other worker remains occupied.
    time.sleep(0.05 if name == 'PCA' else 0.8)
    plot = name + '.html'
    csv = name + '.csv'
    Path(folder, plot).write_text('<html></html>')
    Path(folder, csv).write_text('x\n1\n')
    channel.send({'plot': plot, 'csv': csv, 'metrics': {'execution_time': 0.1}})
    channel.close()


def fake_slow_task(name, params, X, y, folder, channel):
    time.sleep(10)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.uploads = self.root / 'uploads'
        self.results = self.root / 'results'
        self.uploads.mkdir()
        self.results.mkdir()
        iris = load_iris(as_frame=True).frame
        iris.to_csv(self.uploads / 'iris.csv', index=False)
        self.app = create_app()
        self.app.instance_path = str(self.root / 'instance')
        self.app.config.update(TESTING=True, SECRET_KEY='test-key', UPLOAD_FOLDER=str(self.uploads),
                               RESULTS_FOLDER=str(self.results), COMPARISON_WORKERS=2, COMPARISON_TIMEOUT=30)
        self.path = store.database_path(self.app)
        self.token = 'executor'
        self.assertTrue(store.acquire_executor(self.path, self.token))
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['uploaded_filename'] = 'iris.csv'
            session['language'] = 'en'
        response = self.client.get('/comparacoes')
        self.assertIn('value="html" selected', response.get_data(as_text=True))

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, **overrides):
        with self.client.session_transaction() as session:
            csrf = session['comparison_csrf']
        form = {'csrf_token': csrf, 'sample_rate': '100', 'target_column': 'target',
                'dimension': '2', 'scaler': 'standard', 'plot_type': 'html', 'plot_title': ''}
        form.update(overrides)
        response = self.client.post('/comparacoes', data=form)
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        return response.headers['Location'].rsplit('/', 1)[-1]

    def status(self, job_id):
        store.heartbeat(self.path, self.token)
        return self.client.get('/api/comparacoes/' + job_id).get_json()

    def test_progress_reload_isolation_and_history_protection(self):
        job_id = self.submit()
        self.assertEqual(self.client.get('/comparacoes/' + job_id).status_code, 200)
        self.assertEqual(self.app.test_client().get('/api/comparacoes/' + job_id).status_code, 404)
        self.assertIsNone(store.clear_history(self.path, [str(self.uploads), str(self.results)]))
        job = store.claim_next(self.path)
        with patch('app.comparison.worker.execute_task', fake_execute_task):
            thread = threading.Thread(target=run_job, args=(self.app, self.path, job, threading.Event()))
            thread.start()
            deadline = time.monotonic() + 30
            saw_partial = False
            while thread.is_alive() and time.monotonic() < deadline:
                snapshot = self.status(job_id)
                states = [item['status'] for item in snapshot['algorithms']]
                if 'completed' in states and any(s in ('queued', 'running') for s in states):
                    saw_partial = True
                    break
                time.sleep(0.1)
            thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertTrue(saw_partial, 'a completed result should be visible before all tasks finish')
        snapshot = self.status(job_id)
        self.assertEqual(snapshot['status'], 'completed')
        self.assertEqual(snapshot['dataset_name'], 'iris.csv')
        self.assertEqual(snapshot['attributes'], ['sepal length (cm)', 'sepal width (cm)', 'petal length (cm)', 'petal width (cm)'])
        with self.client.session_transaction() as session:
            session['uploaded_filename'] = 'other.csv'
        self.assertEqual(self.status(job_id)['dataset_name'], 'iris.csv')
        self.assertEqual(len(snapshot['algorithms']), 7)
        self.assertTrue(all(item['plot_url'] and item['csv_url'] for item in snapshot['algorithms']))
        self.assertEqual(self.client.get('/comparacoes/' + job_id).status_code, 200)
        self.assertIsNotNone(store.clear_history(self.path, [str(self.uploads), str(self.results)]))

    def test_invalid_advanced_parameter_does_not_block_others(self):
        job_id = self.submit(**{'advanced-T-SNE-perplexity': '999'})
        snapshot = self.status(job_id)
        tasks = {item['algorithm']: item for item in snapshot['algorithms']}
        self.assertEqual(tasks['T-SNE']['status'], 'failed')
        self.assertIn('Perplexity', tasks['T-SNE']['error'])
        self.assertEqual(tasks['PCA']['status'], 'queued')

    def test_portuguese_pages_and_form_validation(self):
        with self.client.session_transaction() as session:
            session['language'] = 'pt'
        response = self.client.get('/comparacoes')
        self.assertIn('Comparar todos os algoritmos', response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            csrf = session['comparison_csrf']
        response = self.client.post('/comparacoes', data={'csrf_token': csrf, 'sample_rate': '101'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('entre 1 e 100', response.get_data(as_text=True))
        job_id = self.submit()
        response = self.client.get('/comparacoes/' + job_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Comparação de algoritmos', response.get_data(as_text=True))
        self.assertIn('{done} de {total} algoritmos finalizaram', response.get_data(as_text=True))

    def test_comparison_layout_controls_are_localized_and_scoped(self):
        job_id = self.submit()
        response = self.client.get('/comparacoes/' + job_id)
        page = response.get_data(as_text=True)
        self.assertIn('container-fluid comparison-page', page)
        self.assertIn('Graphs per row', page)
        self.assertIn('name="comparison-columns" value="1"', page)
        self.assertIn('name="comparison-columns" value="2" checked', page)
        self.assertIn('name="comparison-columns" value="3"', page)
        with self.client.session_transaction() as session:
            session['language'] = 'pt'
        page = self.client.get('/comparacoes/' + job_id).get_data(as_text=True)
        self.assertIn('Gráficos por linha', page)
        self.assertIn('3 por linha', page)
        individual = self.client.get('/pca').get_data(as_text=True)
        self.assertIn('<main class="container my-4 flex-grow-1">', individual)

    def test_sample_dependent_validation(self):
        X = pd.DataFrame({'a': range(10), 'b': range(10)})
        y = pd.Series([0, 1] * 5)
        self.assertEqual(data_error('T-SNE', {'perplexity': 30}, X, y, 2), 'tsne_perplexity')
        self.assertEqual(data_error('LDA', {'dimension': 2}, X, y, 2), 'lda_dimensions')
        self.assertEqual(data_error('LLE', {'n_neighbors': 10}, X, y, 2), 'neighbors')

    def test_metric_failure_keeps_reduced_data_usable(self):
        with patch('app.algorithms.metrics.silhouette_score', side_effect=ValueError('invalid')):
            metrics = clustering_metrics([[1, 1], [2, 2], [8, 8], [9, 9]], [0, 0, 1, 1])
        self.assertEqual(metrics['silhouette_score'], 'N/A')
        self.assertIsInstance(metrics['davies_bouldin'], float)

    def test_real_seven_algorithms_on_iris(self):
        job_id = self.submit()
        job = store.claim_next(self.path)
        run_job(self.app, self.path, job, threading.Event())
        snapshot = self.status(job_id)
        errors = {item['algorithm']: item['error'] for item in snapshot['algorithms'] if item['error']}
        self.assertEqual(snapshot['status'], 'completed', errors)
        self.assertEqual(len(list(self.results.glob('*.html'))), 7)
        self.assertEqual(len(list(self.results.glob('*.csv'))), 7)

    def test_xlsx_dataset_and_existing_pca_flow(self):
        frame = load_iris(as_frame=True).frame
        frame.to_excel(self.uploads / 'iris.xlsx', index=False)
        with self.client.session_transaction() as session:
            session['uploaded_filename'] = 'iris.xlsx'
        response = self.client.post('/pca', data={
            'sample_rate': '100', 'target_column': 'target', 'dimension': '2',
            'scaler': 'none', 'plot_type': 'html', 'plot_title': ''
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('Download Reduced Data', response.get_data(as_text=True))
        self.assertIn('iris.xlsx', response.get_data(as_text=True))
        self.assertIn('sepal length (cm); sepal width (cm); petal length (cm); petal width (cm)', response.get_data(as_text=True))
        job_id = self.submit()
        job = store.claim_next(self.path)
        with patch('app.comparison.worker.execute_task', fake_execute_task):
            run_job(self.app, self.path, job, threading.Event())
        self.assertEqual(self.status(job_id)['status'], 'completed')

    def test_preprocessing_failure_does_not_leave_a_running_job(self):
        pd.DataFrame({'target': [0, 1, 2] * 15, 'text': ['x'] * 45}).to_csv(self.uploads / 'bad.csv', index=False)
        with self.client.session_transaction() as session:
            session['uploaded_filename'] = 'bad.csv'
        job_id = self.submit()
        job = store.claim_next(self.path)
        with self.assertLogs('app.comparison.worker', level='ERROR'):
            run_job(self.app, self.path, job, threading.Event())
        result = self.status(job_id)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(all(item['status'] == 'failed' for item in result['algorithms']))

    def test_slow_algorithm_times_out(self):
        self.app.config['COMPARISON_TIMEOUT'] = 1
        job_id = self.submit()
        job = store.claim_next(self.path)
        for name in store.ALGORITHMS:
            if name != 'PCA':
                store.update_task(self.path, job_id, name, 'failed', error='invalid')
        with patch('app.comparison.worker.execute_task', fake_slow_task):
            run_job(self.app, self.path, job, threading.Event())
        result = self.status(job_id)
        pca = next(item for item in result['algorithms'] if item['algorithm'] == 'PCA')
        self.assertEqual(pca['status'], 'failed')
        self.assertIn('time limit', pca['error'])
        self.assertEqual(result['status'], 'failed')

    def test_executor_interruption_preserves_completed_items(self):
        job_id = self.submit()
        job = store.claim_next(self.path)
        store.update_task(self.path, job['id'], 'PCA', 'completed', metrics={}, plot='one.html', csv='one.csv')
        store.release_executor(self.path, self.token)
        snapshot = self.client.get('/api/comparacoes/' + job_id).get_json()
        states = {item['algorithm']: item['status'] for item in snapshot['algorithms']}
        self.assertEqual(snapshot['status'], 'interrupted')
        self.assertEqual(states['PCA'], 'completed')
        self.assertEqual(states['KPCA'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
