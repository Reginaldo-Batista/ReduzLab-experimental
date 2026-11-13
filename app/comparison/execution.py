"""Run one reducer and produce files without a request context."""
import os
import tempfile

from threadpoolctl import threadpool_limits
from app.algorithms.pca import PCA
from app.algorithms.kpca import KPCA
from app.algorithms.tsne import TSNE
from app.algorithms.lda import LDA
from app.algorithms.nca import NCA
from app.algorithms.lle import LLE
from app.algorithms.isomap import Isomap
from app.core.visualizer import Visualizer

REDUCERS = {'PCA': PCA, 'KPCA': KPCA, 'T-SNE': TSNE, 'LDA': LDA,
            'NCA': NCA, 'LLE': LLE, 'Isomap': Isomap}


def data_error(name, params, X, y, original_classes):
    if name in ('LDA', 'NCA') and original_classes > 20:
        return 'target_classes'
    if name == 'LDA' and (y.nunique() - 1 < params['dimension'] or X.shape[1] < params['dimension']):
        return 'lda_dimensions'
    if name == 'T-SNE' and params['perplexity'] >= len(X):
        return 'tsne_perplexity'
    if name in ('LLE', 'Isomap') and params['n_neighbors'] >= len(X):
        return 'neighbors'
    return None


def execute_task(name, params, X, y, folder, channel):
    """Send only small metadata back; the plot and CSV stay on disk."""
    try:
        with threadpool_limits(limits=1):
            params = dict(params, n_jobs=1)
            results, error = REDUCERS[name](params).fit_transform(X, y)
            if error:
                channel.send({'error': str(error)})
                return
            with tempfile.TemporaryDirectory(prefix='.comparison-', dir=folder) as temp_folder:
                with open(os.path.join(temp_folder, '.reduzlab-temporary'), 'w', encoding='utf-8') as marker:
                    marker.write('comparison')
                visualizer = Visualizer(name, results['reduced_data'], y, params, results_folder=temp_folder)
                plot = visualizer.save_plot()
                csv = visualizer.save_reduced_data()
                os.replace(os.path.join(temp_folder, plot), os.path.join(folder, plot))
                try:
                    os.replace(os.path.join(temp_folder, csv), os.path.join(folder, csv))
                except OSError:
                    os.remove(os.path.join(folder, plot))
                    raise
            metrics = {key: value for key, value in results.items() if key != 'reduced_data'}
            channel.send({'plot': plot, 'csv': csv, 'metrics': metrics})
    except Exception as exc:
        channel.send({'error': str(exc)})
    finally:
        channel.close()
