"""Metrics are optional and must not prevent a valid reduction being shown."""
from sklearn.metrics import davies_bouldin_score, silhouette_score


def clustering_metrics(reduced_data, labels):
    result = {'silhouette_score': 'N/A', 'davies_bouldin': 'N/A'}
    if labels is None:
        return result
    try:
        count = len(set(labels))
        if not 1 < count < len(labels):
            return result
        for key, metric in (('silhouette_score', silhouette_score),
                            ('davies_bouldin', davies_bouldin_score)):
            try:
                result[key] = float(metric(reduced_data, labels))
            except Exception:
                # A failed optional metric must not hide a successful projection.
                pass
    except (ValueError, TypeError):
        pass
    return result
