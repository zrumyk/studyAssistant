import json
from datetime import datetime, timezone
import httpx
import keyring
import base64
import hashlib
from pathlib import Path
from . import db, config
from .providers import SYSTEM

SERVICE = "StudyAssistant.Gemini"
PROVIDERS = {"gemini": "Google Gemini API", "deepseek": "DeepSeek API"}


def key(provider=None):
    provider = provider or config.settings()["cloud_provider"]
    if provider == "deepseek":
        from .credentials import deepseek_key

        return deepseek_key()
    if provider != "gemini":
        raise ValueError("Невідомий хмарний провайдер")
    return keyring.get_password(SERVICE, "api-key")


def month():
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m")


def spent(c, period):
    return c.execute(
        "SELECT coalesce(sum(coalesce(a.corrected_estimate,r.actual,r.reserved)),0) FROM cloud_requests r LEFT JOIN cloud_cost_adjustments a ON a.request_id=r.id WHERE r.month=? AND r.state IN ('sending','done','uncertain')",
        (period,),
    ).fetchone()[0]


def image_payloads(paths):
    from PIL import Image
    if len(paths)>2:
        raise ValueError('До одного запиту підключайте не більше двох рисунків')
    result=[]
    for value in paths:
        path=Path(value).resolve()
        if not path.is_relative_to(config.DATA.resolve()) or not path.is_file() or path.stat().st_size>5_000_000:
            raise ValueError('Рисунок має бути файлом даних програми до 5 МБ')
        with Image.open(path) as image:
            mime={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp','GIF':'image/gif'}.get(image.format)
            if not mime or max(image.size)>8192:
                raise ValueError('Непідтримуваний формат або розмір рисунка')
            width,height=image.size
            image.verify()
        raw=path.read_bytes()
        result.append({'name':path.name,'mime':mime,'width':width,'height':height,
                       'sha256':hashlib.sha256(raw).hexdigest(),'base64':base64.b64encode(raw).decode('ascii')})
    return result


def input_token_bound(prompt,attachments=None,response_schema=None):
    schema_text=json.dumps(response_schema,ensure_ascii=False) if response_schema else ''
    return len((SYSTEM+prompt+schema_text).encode('utf-8'))+4096+1024*len(attachments or [])


def budgeted_output_limit(prompt, attachments=None,response_schema=None):
    """Fit the conservative reservation without changing price or permission limits."""
    import math
    settings=config.settings()
    input_bound=input_token_bound(prompt,attachments,response_schema)
    available=settings['cloud_request_limit_usd']-input_bound*settings['cloud_input_price']/1e6
    if available <= 0:
        raise ValueError('Запит поза межами ліміту: вхідні дані треба розділити на частини')
    price=settings['cloud_output_price']
    count=min(settings['cloud_max_output'],math.floor(available*1e6/price)-1) if price else settings['cloud_max_output']
    if count < min(1024,settings['cloud_max_output']):
        raise ValueError('Запит поза межами ліміту: недостатньо бюджету для цілісної відповіді')
    return count


def quote(pid, prompt, purpose, *,json_response=False,attachments=None,max_output_tokens=None,response_schema=None,thinking_override=None):
    s = config.settings()
    if thinking_override is not None:
        if not isinstance(thinking_override,bool):
            raise ValueError('Thinking override must be boolean')
        s['cloud_thinking']=thinking_override
    attachments=attachments or []
    if response_schema and s['cloud_provider']!='deepseek':
        raise ValueError('Strict response schemas are supported by DeepSeek adapter')
    if max_output_tokens is not None:
        if not isinstance(max_output_tokens,int) or not 256<=max_output_tokens<=s['cloud_max_output']:
            raise ValueError('Ліміт відповіді не може перевищувати налаштування')
        s['cloud_max_output']=max_output_tokens
    if attachments and (s['cloud_provider']!='deepseek' or s['cloud_model'] not in ('deepseek-flash','deepseek-v4-flash-vision-exp')):
        raise ValueError('Передавання зображень реалізовано для DeepSeek Flash')
    if s["cloud_provider"] not in PROVIDERS:
        raise ValueError("Невідомий хмарний провайдер")
    date = datetime.fromisoformat(s["cloud_prices_date"]).date()
    if (datetime.now(timezone.utc).date() - date).days > 30:
        raise ValueError(
            "Оновіть дату й тарифи хмарної моделі за офіційною сторінкою перед платним викликом"
        )
    # UTF-8 byte count is a conservative input-token bound, including system overhead.
    reserve = (
        input_token_bound(prompt,attachments,response_schema) * s["cloud_input_price"]
        + s["cloud_max_output"] * s["cloud_output_price"]
    ) / 1e6
    rid = db.uid()
    period = month()
    project = db.project(pid)
    snapshot = {k: s[k] for k in config.DEFAULTS}
    snapshot["project_revision"] = project["revision"]
    snapshot["approved_plan_hash"] = project["approved_hash"]
    snapshot['json_response']=bool(json_response)
    snapshot['response_schema']=response_schema
    snapshot['image_attachments']=attachments
    snapshot['image_token_upper_bound']=1024*len(attachments)
    with db.connect() as c:
        used = spent(c, period)
        if used + reserve > s["cloud_budget"]:
            raise ValueError("Недостатньо місячного бюджету")
        c.execute(
            "INSERT INTO cloud_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                pid,
                prompt,
                s["cloud_model"],
                purpose,
                period,
                reserve,
                None,
                "quoted",
                None,
                json.dumps(snapshot),
                db.now(),
            ),
        )
    return {
        "id": rid,
        "provider": s["cloud_provider"],
        "provider_name": PROVIDERS[s["cloud_provider"]],
        "model": s["cloud_model"],
        "purpose": purpose,
        "attachments":[{k:v for k,v in item.items() if k!='base64'} for item in attachments],
        "prompt": prompt,
        "maximum_estimate_usd": reserve,
        "spent_usd": used,
        "budget_usd": s["cloud_budget"],
        "pricing_note": "Для DeepSeek використовується консервативна оцінка за піковими тарифами без знижок кешу."
        if s["cloud_provider"] == "deepseek"
        else "",
    }


def approve_and_enqueue(rid, standing=False):
    """Only the explicit, authenticated send action grants this one-use approval."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM cloud_requests WHERE id=?", (rid,)).fetchone()
        if not row or row["state"] != "quoted":
            raise ValueError("Дозвіл уже використано або запит не існує")
        current = config.settings()
        if standing and (
            current["cloud_consent_mode"] != "budgeted"
            or row["reserved"] > current["cloud_request_limit_usd"]
        ):
            raise ValueError(
                "Запит поза межами загального дозволу або ліміту одного звернення"
            )
        if (
            datetime.now(timezone.utc) - datetime.fromisoformat(row["created"])
        ).total_seconds() > 1800:
            raise ValueError("Оцінка застаріла. Створіть новий запит")
        jid = db.uid()
        snapshot = json.loads(row["config"])
        snapshot["approval_kind"] = "standing" if standing else "single"
        c.execute(
            "UPDATE cloud_requests SET state='approved',config=? WHERE id=?",
            (json.dumps(snapshot), rid),
        )
        c.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
            (
                jid,
                row["project_id"],
                "cloud",
                "queued",
                json.dumps({"id": rid}, ensure_ascii=False),
                None,
                None,
                db.now(),
                db.now(),
            ),
        )
    return jid


def response_text_and_usage(provider, payload,expected_tool=None,allow_json_fallback=False):
    if provider == "deepseek":
        choices = payload.get("choices", [])
        first = choices[0] if choices else {}
        if expected_tool:
            calls=first.get('message',{}).get('tool_calls') or []
            if not calls and allow_json_fallback:
                content=first.get('message',{}).get('content') or ''
                try:
                    value=json.loads(content)
                    if isinstance(value,dict):
                        return content,payload.get('usage',{}),first.get('finish_reason')
                except (ValueError,TypeError):
                    pass
            valid=len(calls)==1 and calls[0].get('type')=='function' and calls[0].get('function',{}).get('name')==expected_tool
            text=calls[0]['function'].get('arguments','') if valid else ''
            if not isinstance(text,str):
                text=''
            return text,payload.get('usage',{}),first.get('finish_reason')
        return (
            first.get("message", {}).get("content") or "",
            payload.get("usage", {}),
            first.get("finish_reason"),
        )
    candidates = payload.get("candidates", [])
    text = "\n".join(
        p.get("text", "")
        for candidate in candidates
        for p in candidate.get("content", {}).get("parts", [])
        if not p.get("thought")
    )
    return (
        text,
        payload.get("usageMetadata", {}),
        candidates[0].get("finishReason") if candidates else None,
    )


def usage_cost(provider, usage, settings, reserved,at=None):
    if not usage:
        return reserved
    if provider == "deepseek":
        # completion_tokens already includes reasoning tokens; never count twice.
        inputs = usage.get("prompt_tokens")
        outputs = usage.get("completion_tokens")
    else:
        inputs = usage.get("promptTokenCount")
        outputs = usage.get("candidatesTokenCount", 0) + usage.get(
            "thoughtsTokenCount", 0
        )
    if inputs is None or outputs is None:
        return reserved
    if any(isinstance(v,bool) or not isinstance(v,int) or v<0 for v in (inputs,outputs)):
        return reserved
    input_cost=inputs*settings['cloud_input_price']
    factor=1
    if provider=='deepseek':
        cached_price=settings.get('cloud_cache_input_price')
        hit=usage.get('prompt_cache_hit_tokens',usage.get('prompt_tokens_details',{}).get('cached_tokens'))
        miss=usage.get('prompt_cache_miss_tokens')
        if cached_price is not None and isinstance(hit,int) and not isinstance(hit,bool) and 0<=hit<=inputs and (miss is None or miss==inputs-hit):
            input_cost=hit*cached_price+(inputs-hit)*settings['cloud_input_price']
        if settings.get('cloud_deepseek_offpeak') and settings.get('cloud_model') in ('deepseek-flash','deepseek-v4-flash','deepseek-v4-flash-vision-exp'):
            try:
                when=datetime.fromtimestamp(at,timezone.utc) if isinstance(at,(int,float)) else datetime.fromisoformat(at)
                if when.tzinfo is not None:
                    when=when.astimezone(timezone.utc)
                    peak=when.weekday()<5 and (1<=when.hour<4 or 6<=when.hour<10)
                    factor=1 if peak else .5
            except (ValueError,TypeError,OverflowError,OSError):pass
            # Chinese public-holiday exceptions are not guessed; charging the
            # weekday peak in an unknown holiday is a conservative estimate.
    return (
        input_cost
        + outputs * settings["cloud_output_price"]
    ) * factor / 1e6


def send(rid):
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM cloud_requests WHERE id=?", (rid,)).fetchone()
        if not row or row["state"] != "approved":
            raise ValueError(
                "Потрібен окремий дозвіл на цей запит; оцінка вартості не є дозволом"
            )
        r = dict(row)
        s = json.loads(r["config"])
        if (
            s.get("approval_kind") == "standing"
            and config.settings()["cloud_consent_mode"] != "budgeted"
        ):
            raise ValueError("Загальний дозвіл на API відкликано")
        provider = s.get("cloud_provider", "gemini")
        if provider not in PROVIDERS:
            raise ValueError("Невідомий хмарний провайдер")
        secret = key(provider)
        if not secret:
            raise ValueError(
                "Заповніть DEEPSEEK_API_KEY у D:\\StudyAssistant\\.env"
                if provider == "deepseek"
                else "Додайте API-ключ Gemini у налаштуваннях"
            )
        if (
            datetime.now(timezone.utc) - datetime.fromisoformat(r["created"])
        ).total_seconds() > 1800:
            raise ValueError("Оцінка застаріла. Створіть новий запит")
        period = month()
        if spent(c, period) + r["reserved"] > config.settings()["cloud_budget"]:
            raise ValueError("Місячний бюджет вичерпано")
        c.execute(
            "UPDATE cloud_requests SET state='sending',month=? WHERE id=?",
            (period, rid),
        )
    try:
        sent_at=datetime.now(timezone.utc)
        model = r["model"]
        import re

        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", model):
            raise ValueError("Некоректна назва моделі")
        if provider == "deepseek":
            url = "https://api.deepseek.com/chat/completions"
            headers = {"Authorization": "Bearer " + secret}
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": r["prompt"]},
                ],
                "stream": False,
                "max_tokens": s["cloud_max_output"],
                "thinking": {
                    "type": "enabled" if s.get("cloud_thinking", False) else "disabled"
                },
            }
            if s.get('response_schema'):
                url='https://api.deepseek.com/beta/chat/completions'
                if not s.get('cloud_thinking'):
                    body['temperature']=0.15
                body['tools']=[{'type':'function','function':{'name':'submit_result','description':'Return the requested structured data. This function performs no action.','strict':True,'parameters':s['response_schema']}}]
                body['tool_choice']='auto' if s.get('cloud_thinking') else {'type':'function','function':{'name':'submit_result'}}
            if s.get('cloud_thinking'):
                body['reasoning_effort']='low'
            elif s.get('json_response'):
                body['response_format']={'type':'json_object'}
            if s.get('image_attachments'):
                body['messages'][1]['content']=[{'type':'text','text':r['prompt']}]+[
                    {'type':'image_url','image_url':{'url':'data:'+item['mime']+';base64,'+item['base64'],'detail':'original'}}
                    for item in s['image_attachments']]
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {"x-goog-api-key": secret}
            body = {
                "systemInstruction": {"parts": [{"text": SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": r["prompt"]}]}],
                "generationConfig": {"maxOutputTokens": s["cloud_max_output"]},
            }
        with httpx.Client(
            timeout=180, trust_env=False, follow_redirects=False
        ) as client:
            res = client.post(url, headers=headers, json=body)
            res.raise_for_status()
            payload = res.json()
        text, usage, finish = response_text_and_usage(provider, payload,expected_tool='submit_result' if s.get('response_schema') else None,allow_json_fallback=bool(s.get('cloud_thinking')))
        billing_at=payload.get('created') if provider=='deepseek' else None
        actual = usage_cost(provider, usage, s, r["reserved"],at=billing_at or sent_at.isoformat())
        allowed_finish=('tool_calls',) if s.get('response_schema') else ('stop','STOP')
        incomplete = not isinstance(text,str) or not text.strip() or finish not in allowed_finish
        db.execute(
            "UPDATE cloud_requests SET state='done',actual=?,response=? WHERE id=?",
            (actual, text, rid),
        )
        db.message(
            r["project_id"],
            "assistant",
            f"Хмарна відповідь ({PROVIDERS[provider]}, {model}; оцінка за використаними токенами ${actual:.6f}):\n"
            + (
                "Відповідь незавершена або заблокована; автоматичного повтору немає.\n"
                if incomplete
                else ""
            )
            + text,
        )
        return {
            "text": text,
            "cost_usd": actual,
            "provider": provider,
            "complete": not incomplete,
            "usage": usage,
            "billing_at": billing_at or sent_at.isoformat(),
            "finish_reason": finish,
        }
    except httpx.HTTPStatusError as e:
        state = "failed" if 400 <= e.response.status_code < 500 else "uncertain"
        db.execute(
            "UPDATE cloud_requests SET state=?,actual=? WHERE id=?",
            (state, 0 if state == "failed" else None, rid),
        )
        raise RuntimeError(
            f"Хмарний сервіс повернув HTTP {e.response.status_code}; автоматичного повтору не буде"
        ) from None
    except Exception:
        db.execute("UPDATE cloud_requests SET state='uncertain' WHERE id=?", (rid,))
        raise RuntimeError(
            "Не вдалося підтвердити результат хмарного виклику. Резерв бюджету збережено; автоматичного повтору немає"
        ) from None
