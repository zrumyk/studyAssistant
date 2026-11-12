"""One distinct cached reassessment after proven complete but malformed JSON."""
import json
from pydantic import ValidationError
from . import db
from .assistance import BudgetedModel
from .schemas import ContentReview


def report_review(model, prompt):
    if not model.available():
        return model.generate(prompt, ContentReview)
    try:
        return model.remote(prompt, ContentReview)
    except ValidationError as error:
        if not any(item['type']=='json_invalid' for item in error.errors()): raise
        parent = model.last_request_id
        receipt = db.one('SELECT state,response FROM cloud_requests WHERE id=? AND project_id=?', (parent,model.pid))
        jobs = db.query("SELECT result FROM jobs WHERE kind='cloud' AND status='done' AND payload=? ORDER BY created DESC LIMIT 1",
                        (json.dumps({'id':parent},ensure_ascii=False),))
        if not receipt or receipt['state']!='done' or not jobs: raise
        result = json.loads(jobs[0]['result'])
        if result.get('complete') is not True or result.get('text')!=receipt['response']: raise
        # Parent remains immutable. This stable purpose+prompt identifies at most
        # one child across restart/retry; a broken/unknown child is never retried.
        recovery = BudgetedModel(model.pid,model.local,model.purpose+'/json-recovery-v1/'+parent,
                                 prefer_cloud=True,cancelled=model.cancelled)
        intents = db.query("SELECT id FROM events WHERE project_id=? AND kind='report_review_json_recovery' AND json_extract(data,'$.parent_request_id')=? ORDER BY id LIMIT 1", (model.pid,parent))
        if intents:
            recorded = db.query("SELECT data FROM events WHERE project_id=? AND kind='report_review_json_recovery_receipt' AND id>? AND json_extract(data,'$.parent_request_id')=? ORDER BY id LIMIT 1", (model.pid,intents[0]['id'],parent))
            child = json.loads(recorded[0]['data']).get('request_id') if recorded else None
            if not child:
                raise RuntimeError('Відновлення оцінки вже розпочато; результат невідомий, нового запиту немає')
            recovery.last_request_id = child
            return recovery.revalidate_last_response(ContentReview)
        db.event(model.pid,'report_review_json_recovery',{'parent_request_id':parent,'limit':1})
        try:
            return recovery.remote(prompt+'\nЦе одна нова незалежна оцінка після синтаксично невалідної JSON-відповіді. Не успадковуй її висновки. Заново перевір текст за наведеними доказами. Поверни компактний ContentReview; екрануй подвійні лапки у рядках, не вставляй вкладений JSON як неекранований текст. Не прибирай blocking_issues заради валідного формату.',ContentReview)
        finally:
            db.event(model.pid,'report_review_json_recovery_receipt',{'parent_request_id':parent,'request_id':recovery.last_request_id})
