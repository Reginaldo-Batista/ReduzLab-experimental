"""The advanced controls supported by the individual algorithm forms."""
from flask_babel import gettext as _


def advanced_fields():
    # Field dictionaries are built during a request so labels follow its locale.
    def field(key, label, default='', options=None, kind='text'):
        return {'key': key, 'label': label, 'default': default, 'options': options, 'kind': kind}

    random_state = lambda: field('random_state', _('Random State (optional):'), kind='number')
    neighbors = lambda: field('n_neighbors', _('Number of Neighbors:'), '10', kind='number')
    eigen = lambda extra=(): field('eigen_solver', _('Eigen Solver:'), 'auto', ('auto', 'arpack', 'dense') + extra)
    neighbor_algorithm = lambda: field('neighbors_algorithm', _('Neighbors Algorithm:'), 'auto', ('auto', 'brute', 'kd_tree', 'ball_tree'))
    return {
        'PCA': [field('whiten', _('Enable Whitening'), kind='checkbox'),
                field('svd_solver', _('SVD Solver:'), 'auto', ('auto', 'full', 'randomized', 'arpack', 'covariance_eigh')), random_state()],
        'KPCA': [field('kernel', _('Kernel:'), 'linear', ('linear', 'poly', 'rbf', 'sigmoid', 'cosine')),
                 field('degree', _('Degree (for Poly kernel):'), '3', kind='number'),
                 field('gamma', _('Gamma (for RBF, Poly, Sigmoid):')),
                 field('coef0', _('Coef0 (for Poly, Sigmoid):'), '1'), eigen(('randomized',)), random_state()],
        'T-SNE': [field('perplexity', _('Perplexity:'), '30.0'), field('learning_rate', _('Learning Rate:'), 'auto'),
                  field('max_iter', _('Max Iterations:'), '1000', kind='number'),
                  field('early_exaggeration', _('Early Exaggeration:'), '12.0'),
                  field('init', _('Initialization Method:'), 'pca', ('pca', 'random')),
                  field('method', _('Method:'), 'barnes_hut', ('barnes_hut', 'exact')),
                  field('metric', _('Distance Metric:'), 'euclidean', ('euclidean', 'cosine', 'manhattan', 'chebyshev')), random_state()],
        'LDA': [field('solver', _('Solver:'), 'svd', ('svd', 'eigen')), field('shrinkage', _('Shrinkage:'))],
        'NCA': [field('init', _('Initialization Method:'), 'auto', ('auto', 'pca', 'lda', 'identity', 'random')),
                field('max_iter', _('Max Iterations:'), '50', kind='number'), field('tol', _('Tolerance (optional):'), '1e-5'), random_state()],
        'LLE': [neighbors(), field('reg', _('Regularization:'), '0.001'),
                field('method', _('Method:'), 'standard', ('standard', 'hessian', 'modified', 'ltsa')),
                eigen(), neighbor_algorithm(), random_state()],
        'Isomap': [neighbors(), field('metric', _('Distance Metric:'), 'minkowski', ('minkowski', 'cosine', 'manhattan')),
                   eigen(), field('path_method', _('Path Method:'), 'auto', ('auto', 'FW', 'D')), neighbor_algorithm()],
    }
