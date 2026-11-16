"""Human conversation rendering; task/event records remain authoritative and intact.

No model calls, task execution, permission decisions or terminal layout here.
"""
import json

FAILURES = {
    'Step/error/no-progress budget exhausted.': 'Не удалось продвинуться к результату: действия повторялись без полезных изменений. Задание не завершено.',
    'enclosing work deadline expired': 'Не удалось закончить за отведённое время. Сохранённое состояние задания доступно для просмотра.',
    'Runtime/checker interruption; no automatic replay.': 'Выполнение прервано технической ошибкой. Результат не подтверждён; действия с неизвестным исходом автоматически не повторяются.',
    'inference wall-clock deadline': 'Локальная модель не успела закончить ответ за отведённое время. Задание не завершено.',
}


def reply(state, events=(), *, unpublished=False, published=False):
    report = state['report']
    views=[e for e in events if e['kind'] in {'presentation_view','reference_view'}]
    view_text=''
    if views and state['status']=='unverified':
        from .fact_views import render
        from .conversation_refs import render_reference
        rendered=[]
        for e in views:
            value=e['data']
            if e['kind']=='presentation_view' and not value.get('interpretation'):
                origin=next((a for a in events if a['id']==value.get('intent_id') and a['kind']=='intent'),None)
                if origin and origin['data'].get('kind')=='query' and origin['data'].get('comment'):value=value|{'interpretation':origin['data']['comment']}
            rendered.append((render if e['kind']=='presentation_view' else render_reference)(value))
        view_text='\n\n'.join(rendered)
        if not published and not unpublished:return view_text
    if state['status'] == 'paused':
        return 'Задание приостановлено. Его состояние сохранено; выполнение можно продолжить.'
    if state['status'] == 'cancelled':
        return 'Выполнение задания отменено. Уже совершённые действия сохранены в журнале.'
    if state['status'] == 'uncertain':
        return 'После прерывания исход последней операции неизвестен. Сначала нужно сверить фактическое состояние; автоматически повторять действие небезопасно.'
    if state['status'] in {'blocked', 'incomplete'}:
        timeout = any(e['kind'] == 'model' and (e['data'].get('error_category') in {'inference_timeout', 'time_budget'} or e['data'].get('protocol', {}).get('inference_lifecycle', {}).get('deadline_expired')) for e in events)
        if timeout and report in FAILURES:
            report = FAILURES['inference wall-clock deadline']
        else:
            report = FAILURES.get(report, report)
    if unpublished:
        report += '\nИзменения пока сохранены только в черновике и не переданы в целевой каталог.'
    if published and state['status'] == 'unverified':
        report += '\nФайлы опубликованы; полностью подтвердить выполнение всей цели независимой проверкой пока нельзя.'
    return report+('\n\n'+view_text if view_text else '')


def message(text):
    """Render older saved deliveries without rewriting historical SQLite rows."""
    for prefix in ('\n[Состояние задачи: ',):
        if prefix in text and text.endswith('.]'):
            text = text.rsplit(prefix, 1)[0]
    for old, new in (
        ('[Изменения опубликованы; полная независимая проверка пользовательской цели не задана.]', 'Файлы опубликованы; полная независимая проверка цели не задана.'),
        ('[В рабочем черновике остаются неопубликованные изменения; в целевой каталог они пока не переданы.]', 'Изменения пока находятся в черновике и не переданы в целевой каталог.'),
    ):
        text = text.replace(old, new)
    return FAILURES.get(text, text)


def details(store, task):
    from .persistent import status
    return json.dumps({'task': status(store, task), 'workspace': str(store.workspace(task)),
                       'events': [e for e in store.events(task) if e['kind'] not in {'model', 'inference_request'}]}, ensure_ascii=False, indent=2)
