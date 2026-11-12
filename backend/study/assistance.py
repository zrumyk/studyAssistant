"""Budget-authorized fallback inside the single worker, with durable receipts."""

import hashlib
import json
from pydantic import ValidationError
from . import cloud, config, db
from .providers import ModelOutputTruncated


def structured_response(text,schema,normalized=None):
    """Ignore only known empty provider closing tags, never an extra payload."""
    import re
    try:
        value,end=json.JSONDecoder().raw_decode(text)
    except ValueError:
        return schema.model_validate_json(text)
    suffix=text[end:]
    # A completed tool response can contain one redundant root terminator.
    # Parse the whole first object, never repair an unfinished JSON value or
    # accept a second payload. The original paid response remains immutable.
    if isinstance(value,dict) and suffix.strip()=='}':
        result=schema.model_validate_json(text[:end])
        if normalized:normalized('redundant_final_brace')
        return result
    if suffix and re.fullmatch(r'(?:\s|\\n|</｜｜DSML｜｜ (?:parameter|invoke|calls)>)+',suffix):
        text=text[:end]
    return schema.model_validate_json(text)


class BudgetedModel:
    def __init__(self, pid, local, purpose, prefer_cloud=False, cancelled=None, allow_remote_images=False,thinking_override=None,bounded_thinking_fallback=False,max_output_tokens=None,min_output_tokens=None):
        self.pid = pid
        self.local = local
        self.purpose = purpose
        self.prefer_cloud = prefer_cloud
        self.last_source = "local"
        self.last_request_id = None
        self.cancelled = cancelled or (lambda: False)
        self.allow_remote_images=allow_remote_images
        self.thinking_override=thinking_override
        self.bounded_thinking_fallback=bounded_thinking_fallback
        if max_output_tokens is not None and (type(max_output_tokens) is not int or max_output_tokens < 256):
            raise ValueError('Ліміт відповіді має бути цілим числом не менше256')
        self.max_output_tokens = max_output_tokens
        if min_output_tokens is not None and (type(min_output_tokens) is not int or min_output_tokens < 256):
            raise ValueError('Мінімальний обсяг відповіді має бути цілим числом не менше256')
        self.min_output_tokens = min_output_tokens

    def remote_with_fallback(self,prompt,schema,images=None):
        try:
            return self.remote(prompt,schema,images=images) if images else self.remote(prompt,schema)
        except ModelOutputTruncated:
            if not self.bounded_thinking_fallback or self.thinking_override is not True:raise
            # One distinct non-thinking request; never concatenate an incomplete
            # answer or bypass the same schema, budget, receipts and cancellation.
            db.event(self.pid,'thinking_output_exhausted',{'purpose':self.purpose,'fallback':'non-thinking-once'})
            self.thinking_override=False
            try:return self.remote(prompt,schema,images=images) if images else self.remote(prompt,schema)
            finally:self.thinking_override=True

    def available(self):
        return config.settings()["cloud_consent_mode"] == "budgeted"

    def generate(self, prompt, schema=None, images=None):
        settings=config.settings()
        remote_images=self.allow_remote_images and settings['cloud_provider']=='deepseek' and settings['cloud_model'] in ('deepseek-flash','deepseek-v4-flash-vision-exp')
        from .schemas import Action,PythonProgram
        structured_action = schema is PythonProgram or (schema is not None and isinstance(schema,type) and issubclass(schema,Action))
        # Long structured actions were taking 15 minutes locally before failure.
        # Use the already-authorized budget for that workload, keep small tasks local.
        if (self.prefer_cloud or structured_action or len(prompt)>7000) and self.available() and (not images or remote_images):
            return self.remote_with_fallback(prompt,schema,images)
        try:
            self.last_request_id = None
            result = (
                self.local.generate(prompt, schema, images=images)
                if images
                else self.local.generate(prompt, schema)
            )
            self.last_source = "local"
            return result
        except (RuntimeError, ValidationError):
            if (images and not remote_images) or not self.available():
                raise
            return self.remote_with_fallback(prompt,schema,images)

    def revalidate_last_response(self, schema):
        """Parse a confirmed complete paid response locally; never send a request."""
        if self.cancelled():
            raise RuntimeError("Генерацію призупинено; повторний розбір не розпочато")
        if not self.last_request_id:
            raise ValueError("Немає квитанції для повторного розбору")
        receipt = db.one("SELECT state FROM cloud_requests WHERE id=? AND project_id=?",
                         (self.last_request_id, self.pid))
        jobs = db.query("SELECT result FROM jobs WHERE kind='cloud' AND status='done' AND payload=? ORDER BY created DESC LIMIT 1",
                        (json.dumps({"id": self.last_request_id}, ensure_ascii=False),))
        if not receipt or receipt['state'] != 'done' or not jobs:
            raise RuntimeError("Результат запиту не підтверджено; повторного запиту немає")
        result = json.loads(jobs[0]['result'])
        if not result.get('complete'):
            raise ModelOutputTruncated("Незавершену відповідь не можна приймати повторним розбором")
        text = result['text'].strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        value = structured_response(text, schema)
        db.event(self.pid, 'paid_response_revalidated', {'request_id': self.last_request_id, 'schema': schema.__name__})
        return value

    def remote(self, prompt, schema=None,images=None):
        if self.cancelled():
            raise RuntimeError("Генерацію призупинено; хмарний запит не відправлено")
        if not self.available():
            raise RuntimeError("Автоматичне хмарне підсилення вимкнено")
        if images and not self.allow_remote_images:
            raise ValueError('Цей інструмент не має дозволу передавати рисунки в хмару')
        attachments=cloud.image_payloads(images) if images else []
        project = db.project(self.pid)
        s=config.settings()
        if self.max_output_tokens is not None and self.max_output_tokens > s['cloud_max_output']:
            raise ValueError('Ліміт відповіді не може перевищувати налаштування')
        effective_thinking=s['cloud_thinking'] if self.thinking_override is None else self.thinking_override
        from .structured import deepseek_schema
        response_schema=deepseek_schema(schema.model_json_schema()) if schema and s['cloud_provider']=='deepseek' else None
        schema_text = (
            json.dumps(schema.model_json_schema(), ensure_ascii=False) if schema else ""
        )
        request_text = prompt + (
            '\nSubmit one submit_result function call with the requested json data.' if response_schema else
            "\nReturn ONLY a valid json object matching this JSON Schema, no Markdown fences: "
            + schema_text
            if schema
            else ""
        )
        s = config.settings()
        output_limit=cloud.budgeted_output_limit(request_text,attachments,response_schema)
        if self.max_output_tokens is not None:
            output_limit = min(output_limit, self.max_output_tokens)
        key = hashlib.sha256(
            json.dumps(
                [
                    project["revision"],
                    project["approved_hash"],
                    s["cloud_provider"],
                    s["cloud_model"],
                    effective_thinking,
                    s["cloud_max_output"],
                    output_limit,
                    ('strict-tool-v5-thinking-auto-json-low' if effective_thinking else 'strict-tool-v2-lowtemp') if response_schema else ('native-json-mode-v1' if schema else 'text-v1'),
                    response_schema,
                    [item['sha256'] for item in attachments],
                    request_text,
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        purpose = "auto/" + key + "/" + self.purpose[:100]
        existing = db.query(
            "SELECT id,state,response FROM cloud_requests WHERE project_id=? AND purpose=? ORDER BY created DESC LIMIT 1",
            (self.pid, purpose),
        )
        if existing and existing[0]["state"] != "quoted":
            receipt = existing[0]
            self.last_request_id = receipt['id']
            jobs = db.query(
                "SELECT result FROM jobs WHERE kind=? AND status=? AND payload=? ORDER BY created DESC LIMIT 1",
                (
                    "cloud",
                    "done",
                    json.dumps({"id": receipt["id"]}, ensure_ascii=False),
                ),
            )
            if receipt["state"] != "done" or not jobs:
                raise RuntimeError(
                    "Попередній хмарний запит має непідтверджений результат; автоматичного повтору немає"
                )
            result = json.loads(jobs[0]["result"])
        else:
            if self.min_output_tokens is not None and output_limit < self.min_output_tokens:
                raise ValueError(f'Недостатній обсяг відповіді до відправлення: доступно{output_limit}, потрібно щонайменше{self.min_output_tokens} tokens. Скоротіть дублювання запиту або розділіть роботу; бюджет не змінено.')
            quote = cloud.quote(self.pid,request_text,purpose,json_response=bool(schema),attachments=attachments,max_output_tokens=output_limit,response_schema=response_schema,thinking_override=self.thinking_override)
            self.last_request_id = quote['id']
            jid = cloud.approve_and_enqueue(quote["id"], standing=True)
            # The single worker executes its own child request inline. Waiting
            # for the regular queue here would deadlock that same worker.
            db.execute(
                "UPDATE jobs SET status='running',updated=? WHERE id=? AND status='queued'",
                (db.now(), jid),
            )
            try:
                result = cloud.send(quote["id"])
            except Exception as error:
                db.execute(
                    "UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?",
                    (str(error)[:1500], db.now(), jid),
                )
                raise
            db.execute(
                "UPDATE jobs SET status='done',result=?,updated=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), db.now(), jid),
            )
            receipt = {"id": quote["id"]}
        self.last_source = "cloud/" + s["cloud_provider"] + "/" + s["cloud_model"]
        self.last_request_id = receipt['id']
        db.event(
            self.pid,
            "automatic_assistance",
            {
                "request_id": receipt["id"],
                "purpose": self.purpose,
                "complete": result["complete"],
                "source": self.last_source,
            },
        )
        if not result["complete"]:
            raise ModelOutputTruncated(
                "Хмарна відповідь незавершена; повтор того самого запиту не відправляється"
            )
        text = result["text"].strip()
        if schema:
            if text.startswith("```") and text.endswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return structured_response(text,schema,normalized=lambda reason:db.event(self.pid,'structured_wrapper_normalized',{'request_id':self.last_request_id,'reason':reason}))
        return text
