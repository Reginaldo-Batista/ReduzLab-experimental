"""Configuration, submission and session-scoped progress for comparisons."""
import hmac
import json
import uuid

from flask import abort, current_app, jsonify, redirect, render_template, request, session, url_for
from flask_babel import gettext as _
from werkzeug.datastructures import MultiDict

from app.comparison import store
from app.comparison.fields import advanced_fields
from app.main import bp
from app.utils.decorators import require_dataset
from app.utils.form_validator import validate_base_parameters
from app.utils.algorith_parameters_validation.pca_parameter_validation import validate_pca_parameters
from app.utils.algorith_parameters_validation.kpca_parameter_validation import validate_kpca_parameters
from app.utils.algorith_parameters_validation.tsne_parameter_validation import validate_tsne_parameters
from app.utils.algorith_parameters_validation.lda_parameter_validation import validate_lda_parameters
from app.utils.algorith_parameters_validation.nca_parameter_validation import validate_nca_parameters
from app.utils.algorith_parameters_validation.lle_parameter_validation import validate_lle_parameters
from app.utils.algorith_parameters_validation.isomap_parameter_validation import validate_isomap_parameters

VALIDATORS = {'PCA': validate_pca_parameters, 'KPCA': validate_kpca_parameters,
              'T-SNE': validate_tsne_parameters, 'LDA': validate_lda_parameters,
              'NCA': validate_nca_parameters, 'LLE': validate_lle_parameters,
              'Isomap': validate_isomap_parameters}


def owner_id():
    if 'comparison_owner' not in session:
        session['comparison_owner'] = uuid.uuid4().hex
    return session['comparison_owner']


def render_form(df, table_html, validation_results, error=None, status=200):
    token = session.setdefault('comparison_csrf', uuid.uuid4().hex)
    return render_template('comparison_form.html', column_options=df.columns.tolist(),
                           table_html=table_html, validation_results=validation_results,
                           form_data=request.form if request.method == 'POST' else {'plot_type': 'html'},
                           algorithms=store.ALGORITHMS,
                           fields=advanced_fields(), error=error, csrf_token=token), status


@bp.route('/comparacoes', methods=['GET', 'POST'])
@require_dataset
def comparison_form(df, table_html, validation_results):
    if request.method == 'GET':
        return render_form(df, table_html, validation_results)
    supplied = request.form.get('csrf_token', '')
    if not supplied or not hmac.compare_digest(supplied.encode('utf-8'), session.get('comparison_csrf', '').encode('utf-8')):
        abort(400)
    common, error = validate_base_parameters(request.form, df)
    if error:
        return render_form(df, table_html, validation_results, error, 400)

    params_by_algorithm, errors = {}, {}
    fields = advanced_fields()
    for name in store.ALGORITHMS:
        form = MultiDict(request.form)
        for field in fields[name]:
            key = field['key']
            form.pop(key, None)
            value = request.form.get(f'advanced-{name}-{key}')
            if value is not None:
                form[key] = value
        params, error = VALIDATORS[name](form, df)
        if error:
            errors[name] = str(error)
        else:
            params['n_jobs'] = 1
            params_by_algorithm[name] = params
    job_id = store.enqueue(store.database_path(current_app), owner_id(), session['uploaded_filename'], common,
                           params_by_algorithm, errors, session.get('language', 'en'))
    if not job_id:
        return render_form(df, table_html, validation_results,
                           _('The comparison executor is unavailable. Please try again shortly.'), 503)
    return redirect(url_for('main.comparison_results', job_id=job_id), code=303)


def authorized_job(job_id):
    owner = session.get('comparison_owner')
    job = store.get_job(store.database_path(current_app), job_id, owner) if owner else None
    if not job:
        abort(404)
    return job


@bp.get('/comparacoes/<job_id>')
def comparison_results(job_id):
    authorized_job(job_id)
    return render_template('comparison_results.html', job_id=job_id, algorithms=store.ALGORITHMS)


def error_message(code):
    messages = {
        'interrupted': _('The executor stopped before this algorithm finished.'),
        'target_classes': _('The target has more than 20 classes; select a categorical target for this supervised algorithm.'),
        'lda_dimensions': _('LDA needs more target classes than output dimensions and enough numeric features.'),
        'tsne_perplexity': _('T-SNE perplexity must be lower than the number of samples after sampling.'),
        'neighbors': _('The number of neighbors must be lower than the number of samples after sampling.'),
        'execution_error': _('The algorithm or graph generation failed. Check the parameters and server log.'),
        'preprocessing_error': _('The dataset could not be prepared. Check its values and the selected parameters.'),
        'timeout': _('The algorithm exceeded the execution time limit.'),
    }
    if code.startswith('execution_error:'):
        return messages['execution_error'] + ' ' + code.split(':', 1)[1]
    return messages.get(code, code)


@bp.get('/api/comparacoes/<job_id>')
def comparison_status(job_id):
    job, tasks = authorized_job(job_id)
    items = []
    for name in store.ALGORITHMS:
        task = tasks[name]
        items.append({'algorithm': name, 'status': task['status'],
                      'error': error_message(task['error']) if task['error'] else None,
                      'metrics': json.loads(task['metrics']) if task['metrics'] else None,
                      'plot_url': url_for('main.serve_result_file', filename=task['plot']) if task['plot'] else None,
                      'csv_url': url_for('main.serve_result_file', filename=task['csv']) if task['csv'] else None})
    response = jsonify({'id': job_id, 'status': job['status'], 'algorithms': items})
    response.headers['Cache-Control'] = 'no-store'
    return response
