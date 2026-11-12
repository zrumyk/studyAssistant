import React, { useState, useEffect, useCallback } from "react";
import { createRoot } from "react-dom/client";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BookOpen,
  FolderOpen,
  Settings,
  Plus,
  ArrowUpRight,
  ArrowRight,
  Play,
  Pause,
  Check,
  FileText,
  Upload,
  Search,
  Download,
  Cloud,
  ChevronRight,
  CheckCircle2,
  AlertCircle,
  Loader2,
  X,
  Send,
  Layers,
  RefreshCw,
  PenLine,
  ExternalLink,
  GraduationCap,
} from "lucide-react";
import "./style.css";

type Data = Record<string, any>;
let token = "";
async function api(path: string, method = "GET", body?: any) {
  const headers: Record<string, string> = { "x-study-token": token };
  if (body !== undefined && !(body instanceof FormData))
    headers["Content-Type"] = "application/json";
  const r = await fetch("/api" + path, {
    method,
    headers,
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}
const status: Data = {
  analysis: "Нова робота",
  analysis_running: "Аналіз матеріалів",
  awaiting_approval: "Погодьте план",
  running: "Виконується",
  needs_user: "Потрібна ваша дія",
  paused: "Призупинено",
  completed: "План виконано",
  error: "Потрібна увага",
  pending: "У черзі",
  done: "Завершено",
  waiting_user: "Ваша дія",
  failed: "Помилка",
  blocked: "Виконання зупинено",
  queued: "У черзі",
  interrupted: "Перервано",
  cancelled: "Скасовано",
};
const roles: Data = {
  task: "Завдання / методичка",
  theory: "Теорія",
  reference: "Зразок звіту",
  data: "Вихідні дані",
  result: "Мій результат",
};
const stateLabels: Data = {
  created: "Створено",
  executed: "Виконано",
  needs_review: "Потребує перегляду",
  stale: "Застаріло",
};
const kinds: Data = {
  code: "Код та обчислення",
  write: "Підготувати файли",
  manual: "Ручний крок",
  diagram: "Блок-схема плану",
  fetch: "Завантажити дані",
  report: "Зібрати звіт",
  defense: "Підготувати до захисту",
};
const languages = [
  "python",
  "javascript",
  "typescript",
  "c",
  "cpp",
  "java",
  "csharp",
  "r",
  "sql",
  "octave",
];
function Md({ text }: { text: string }) {
  return (
    <div className="markdown">
      <Markdown remarkPlugins={[remarkGfm]}>{text || ""}</Markdown>
    </div>
  );
}
function Badge({ value }: { value: string }) {
  return (
    <span className={"badge " + value}>
      {status[value] || stateLabels[value] || value}
    </span>
  );
}
function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function App() {
  const [ready, setReady] = useState(false),
    [screen, setScreen] = useState("home"),
    [projects, setProjects] = useState<Data[]>([]),
    [selected, setSelected] = useState(""),
    [project, setProject] = useState<Data | null>(null),
    [tab, setTab] = useState("plan"),
    [docs, setDocs] = useState<Data[]>([]),
    [health, setHealth] = useState<Data | null>(null),
    [settings, setSettings] = useState<Data>({}),
    [jobs, setJobs] = useState<Data[]>([]),
    [modal, setModal] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [quote, setQuote] = useState<Data | null>(null),
    [modelProgress, setModelProgress] = useState<Data | null>(null);
  const notify = (text: string) => {
    setNotice(text);
    setTimeout(() => setNotice(""), 6000);
  };
  const run = async (fn: () => Promise<any>) => {
    setError("");
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const load = useCallback(async () => {
    setProjects(await api("/projects"));
    setJobs(await api("/jobs"));
    if (selected) setProject(await api("/projects/" + selected));
  }, [selected]);
  useEffect(() => {
    api("/session")
      .then((s) => {
        token = s.token;
        setReady(true);
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (!ready) return;
    load();
    const timer = setInterval(() => load().catch(() => {}), 6000);
    const es = new EventSource("/api/events");
    es.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data);
        if (event.kind === "model_progress" && Date.now() - Date.parse(event.created) < 60000) {
          setModelProgress({ ...JSON.parse(event.data), project_id: event.project_id });
        }
      } catch { /* A malformed progress event must not interrupt normal refresh. */ }
      load().catch(() => {});
    };
    return () => {
      clearInterval(timer);
      es.close();
    };
  }, [ready, load]);
  useEffect(() => {
    if (ready && screen === "home") {
      api("/health")
        .then(setHealth)
        .catch(() => {});
      api("/settings")
        .then(setSettings)
        .catch(() => {});
    }
    if (ready && screen === "library")
      api("/documents")
        .then(setDocs)
        .catch((e) => setError(e.message));
    if (ready && screen === "settings")
      run(async () => {
        setSettings(await api("/settings"));
        setHealth(await api("/health"));
      });
  }, [ready, screen]);
  useEffect(() => {
    if (
      ready &&
      screen === "library" &&
      !jobs.some((j) => ["queued", "running"].includes(j.status))
    )
      api("/documents")
        .then(setDocs)
        .catch(() => {});
  }, [ready, screen, jobs.map((j) => j.id + j.status).join("|")]);
  const open = (id: string) => {
    setSelected(id);
    setScreen("project");
    setTab("plan");
  };
  const act = (action: string) =>
    run(async () => {
      await api(`/projects/${selected}/${action}`, "POST");
      await load();
      notify(
        action === "approve"
          ? "План погоджено. Можна запускати."
          : "Дію додано до черги",
      );
    });
  const active = project?.jobs?.some((j: Data) =>
    ["queued", "running"].includes(j.status),
  );
  async function uploadFiles(
    files: FileList | null,
    role: string,
    subject: string,
    pid: string,
  ) {
    if (!files) return;
    await run(async () => {
      for (const file of Array.from(files)) {
        const data = new FormData();
        data.append("file", file);
        data.append("role", role);
        data.append("subject", subject);
        data.append("project_id", pid);
        const task = await api("/upload", "POST", data);
        notify("Читаємо " + file.name);
        for (;;) {
          await new Promise((r) => setTimeout(r, 800));
          const list = await api("/jobs");
          const j = list.find((x: Data) => x.id === task.job_id);
          if (j && ["done", "failed", "interrupted"].includes(j.status)) {
            if (j.status !== "done")
              throw new Error(j.error || "Імпорт перервано");
            break;
          }
        }
      }
      await load();
      setDocs(await api("/documents"));
      notify("Матеріали додано");
    });
  }
  const heading =
    screen === "home"
      ? "Ваш простір для навчання"
      : screen === "library"
        ? "Бібліотека матеріалів"
        : screen === "settings"
          ? "Налаштування помічника"
          : project?.title || "Навчальна робота";
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <BookOpen size={24} />
          </div>
          <div>
            майстерня<span>навчальний помічник</span>
          </div>
        </div>
        <div className="nav-label">ВАШ ПРОСТІР</div>
        <nav>
          <button
            className={screen === "home" ? "active" : ""}
            onClick={() => setScreen("home")}
          >
            <Layers size={19} />
            Мої роботи<span className="nav-count">{projects.length}</span>
          </button>
          <button
            className={screen === "library" ? "active" : ""}
            onClick={() => setScreen("library")}
          >
            <BookOpen size={19} />
            Бібліотека
          </button>
          <button
            className={screen === "settings" ? "active" : ""}
            onClick={() => setScreen("settings")}
          >
            <Settings size={19} />
            Налаштування
          </button>
        </nav>
        <div className="nav-label recent-label">ОСТАННІ РОБОТИ</div>
        <div className="recent">
          {projects.slice(0, 7).map((p) => (
            <button
              className={
                selected === p.id && screen === "project" ? "chosen" : ""
              }
              key={p.id}
              onClick={() => open(p.id)}
            >
              <span className={"dot " + p.status} />
              <span>{p.title}</span>
              <ChevronRight size={14} />
            </button>
          ))}
        </div>
        <div className="sidebar-bottom">
          <span className="local-light" />
          <div>
            Локальний простір<small>Ваші роботи зберігаються на ноутбуці</small>
          </div>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            Майстерня <ChevronRight size={14} />{" "}
            {screen === "project"
              ? project?.subject || "Робота"
              : screen === "home"
                ? "Мої роботи"
                : screen === "library"
                  ? "Бібліотека"
                  : "Налаштування"}
          </div>
          <div className="header-right">
            <span className="local-pill">
              <span />
              Ollama · локально
            </span>
            <div className="avatar">
              <GraduationCap size={19} />
            </div>
          </div>
        </header>
        <div className="content">
          <div className="title-row">
            <div>
              <div className="eyebrow">
                {screen === "home"
                  ? "ВІД ЗАВДАННЯ ДО РЕЗУЛЬТАТУ"
                  : screen === "project"
                    ? "НАВЧАЛЬНИЙ ПРОЄКТ"
                    : "ОСОБИСТИЙ ПРОСТІР"}
              </div>
              <h1>{heading}</h1>
              <p className="subtitle">
                {screen === "home"
                  ? "Зберіть матеріали. Погодьте план. Працюйте разом із помічником."
                  : screen === "library"
                    ? "Лекції, методички та зразки звітів — у спільній бібліотеці."
                    : screen === "settings"
                      ? "Моделі, середовище виконання й оформлення ваших робіт."
                      : project?.subject}
              </p>
            </div>
            {screen === "home" && (
              <button className="primary" onClick={() => setModal("create")}>
                <Plus size={18} />
                Нова робота
              </button>
            )}
            {screen === "project" && project && (
              <Badge value={project.status} />
            )}
          </div>
          {error && (
            <div className="alert error">
              <AlertCircle size={19} />
              <span>{error}</span>
              <button
                onClick={() => setError("")}
                aria-label="Закрити повідомлення"
              >
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="alert success">
              <CheckCircle2 size={18} />
              {notice}
            </div>
          )}
          {screen === "home" && (
            <>
              {health && (!health.ollama.available || !health.docker) && (
                <div className="alert warning">
                  <Settings size={19} />
                  <span>
                    <b>Підготуйте середовище перед першою роботою.</b>
                    <br />
                    {!health.ollama.available ? "Ollama ще не запущена. " : ""}
                    {!health.docker
                      ? "Docker потрібен для виконання коду. "
                      : ""}
                    Налаштування покажуть потрібні компоненти та доступне місце.
                  </span>
                  <button
                    className="secondary"
                    onClick={() => setScreen("settings")}
                  >
                    Налаштувати <ArrowRight size={15} />
                  </button>
                </div>
              )}
              <section className="hero">
                <div className="hero-copy">
                  <span className="hero-kicker">ВАШ НАСТУПНИЙ КРОК</span>
                  <h2>
                    Складні завдання.
                    <br />
                    <em>Зрозумілий шлях.</em>
                  </h2>
                  <p>
                    Помічник читає матеріали, пише код, виконує розрахунки та
                    готує звіт. Там, де потрібні ви, — пояснює кожну дію.
                  </p>
                  <button onClick={() => setModal("create")}>
                    Почати нову роботу <ArrowUpRight size={19} />
                  </button>
                </div>
                <div className="hero-visual">
                  <div className="orbit orbit-one" />
                  <div className="orbit orbit-two" />
                  <div className="mini-sheet">
                    <div className="sheet-top">
                      <FileText size={18} />
                      <span>План роботи</span>
                      <span className="sheet-dot" />
                    </div>
                    {[
                      "Зрозуміти завдання",
                      "Підготувати рішення",
                      "Перевірити результат",
                    ].map((t, i) => (
                      <div className="sheet-line" key={t}>
                        <span>{i < 2 ? <Check size={12} /> : i + 1}</span>
                        {t}
                      </div>
                    ))}
                    <div className="sheet-footer">
                      <span />
                      Крок за кроком
                    </div>
                  </div>
                  <div className="floating-note">
                    <CheckCircle2 size={17} />
                    Ви контролюєте план
                  </div>
                </div>
              </section>
              <div className="section-title">
                <h2>
                  Мої роботи <span>{projects.length}</span>
                </h2>
                <span className="muted">
                  Збережений прогрес та всі результати
                </span>
              </div>
              {projects.length ? (
                <div className="project-grid">
                  {projects.map((p) => (
                    <button
                      className="project-card"
                      key={p.id}
                      onClick={() => open(p.id)}
                    >
                      <div className="project-card-top">
                        <div className="file-icon">
                          <FolderOpen size={23} />
                        </div>
                        <ArrowUpRight size={18} />
                      </div>
                      <small>{p.subject || "НАВЧАЛЬНА РОБОТА"}</small>
                      <h3>{p.title}</h3>
                      <div className="project-card-bottom">
                        <Badge value={p.status} />
                        <span>
                          {new Date(p.updated).toLocaleDateString("uk-UA")}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="empty">
                  <div className="empty-icon">
                    <FolderOpen size={30} />
                  </div>
                  <h3>Тут з’являться ваші роботи</h3>
                  <p>
                    Додайте завдання та матеріали — помічник запропонує план
                    виконання.
                  </p>
                  <button
                    className="secondary"
                    onClick={() => setModal("create")}
                  >
                    <Plus size={17} />
                    Створити першу роботу
                  </button>
                </div>
              )}
              <div className="principles">
                <div>
                  <CheckCircle2 size={20} />
                  <span>
                    <b>План під вашим контролем</b>
                    <small>Виконання після погодження</small>
                  </span>
                </div>
                <div>
                  <FileText size={20} />
                  <span>
                    <b>Результати з джерелами</b>
                    <small>Код, файли та історія запусків</small>
                  </span>
                </div>
                <div>
                  <Cloud size={20} />
                  <span>
                      <b>Ви керуєте доступом до хмари</b>
                      <small>Разовий дозвіл або погоджений бюджет</small>
                  </span>
                </div>
              </div>
            </>
          )}
          {screen === "library" && (
            <>
              <SemesterPanel busy={busy} notify={notify} />
              <ImportPanel
                pid=""
                subject=""
                upload={uploadFiles}
                onImport={async (path, role, subject) => {
                  await api("/import", "POST", { path, role, subject });
                  notify("Імпорт додано до черги");
                }}
                busy={busy}
              />
              <div className="panel">
                <div className="panel-heading">
                  <h2>
                    Матеріали <span>{docs.length}</span>
                  </h2>
                  <button
                    className="text-button"
                    onClick={() =>
                      run(async () => {
                        await api("/library/refresh", "POST");
                        setDocs(await api("/documents"));
                        notify(
                          "Папки й файли перевіряються на зміни. Список оновиться після завершення черги.",
                        );
                      })
                    }
                  >
                    <RefreshCw size={15} />
                    Оновити
                  </button>
                </div>
                <DocumentList docs={docs} />
              </div>
            </>
          )}
          {screen === "settings" && (
            <SettingsView
              settings={settings}
              setSettings={setSettings}
              health={health}
              busy={busy}
              save={() =>
                run(async () => {
                  await api("/settings", "PUT", settings);
                  setSettings(await api("/settings"));
                  notify("Налаштування збережено");
                })
              }
              diagnose={() => run(async () => setHealth(await api("/health")))}
              pull={(model) =>
                run(async () => {
                  await api("/models/pull", "POST", { model });
                  notify("Завантаження додано до черги");
                })
              }
              backup={() =>
                run(async () => {
                  const r = await api("/backup", "POST");
                  notify(r.message + " · " + r.path);
                })
              }
            />
          )}
          {screen === "project" && project && (
            <>
              <div className="toolbar">
                <button className="secondary" onClick={() => setModal("task")}>
                  <PenLine size={16} />
                  Завдання
                </button>
                <button
                  className="secondary"
                  disabled={busy || active}
                  onClick={() => act("analyse")}
                >
                  <Search size={16} />
                  Проаналізувати
                </button>
                <button
                  className="primary"
                  disabled={busy || active || !project.approved_hash}
                  onClick={() => act("execute")}
                >
                  <Play size={16} />
                  Виконати / продовжити
                </button>
                <button className="secondary" onClick={() => act("pause")}>
                  <Pause size={16} />
                  Пауза
                </button>
                <a
                  className="icon-button"
                  title="Експортувати роботу"
                  href={`/api/projects/${selected}/export`}
                >
                  <Download size={18} />
                </a>
              </div>
              <div className="tabs">
                {[
                  ["plan", "План роботи"],
                  ["materials", "Матеріали"],
                  ["chat", "Обговорення"],
                  ["files", "Результати"],
                  ["logs", "Журнал"],
                ].map(([id, label]) => (
                  <button
                    key={id}
                    className={tab === id ? "selected" : ""}
                    onClick={() => setTab(id)}
                  >
                    {label}
                    {id === "files" && <span>{project.artifacts.length}</span>}
                  </button>
                ))}
              </div>
              {tab === "plan" && (
                <>
                  {active && modelProgress?.project_id === selected && modelProgress?.phase === "generating" && (
                    <div className="alert" role="status">
                      Модель формує відповідь: {modelProgress.elapsed_seconds} с,
                      отримано {modelProgress.characters} символів. Перевірка почнеться після завершення відповіді.
                    </div>
                  )}
                  <div className="panel task-summary">
                    <span className="eyebrow">
                      ЗАВДАННЯ{" "}
                      {project.variant && "· ВАРІАНТ " + project.variant}
                    </span>
                    <p>{project.task}</p>
                  </div>
                  {project.plan?.goal ? (
                    <>
                      <div className="panel">
                        <div className="panel-heading">
                          <h2>{project.approved_hash ? "Погоджений план" : "План для погодження"}</h2>
                          <button
                            className="text-button"
                            disabled={active}
                            onClick={() => setModal("plan")}
                          >
                            <PenLine size={15} />
                            Редагувати
                          </button>
                        </div>
                        <p>{project.plan.goal}</p>
                        <ul className="requirements">
                          {project.plan.requirements.map(
                            (x: string, i: number) => (
                              <li key={i}>
                                <Check size={14} />
                                {x}
                              </li>
                            ),
                          )}
                        </ul>
                        {[
                          ...project.plan.missing,
                          ...project.plan.conflicts,
                        ].map((x: string, i: number) => (
                          <div className="alert warning" key={i}>
                            <AlertCircle size={17} />
                            {x}
                          </div>
                        ))}
                        {(project.plan.warnings || []).map((x: string, i: number) => (
                          <div className="alert" key={'note-'+i}>
                            <AlertCircle size={17} />
                            <span><b>Зауваження:</b> {x}</span>
                          </div>
                        ))}
                        {(project.plan.conflict_evidence || []).filter((item: Data) => project.plan.conflicts[item.conflict_index]).map((item: Data) => (
                          <details className="source" key={'conflict-'+item.conflict_index}>
                            <summary>Джерела суперечності {item.conflict_index + 1}</summary>
                            {[item.source_a, item.source_b].map((source: Data, i: number) => (
                              <div key={i}>
                                <a href={`/api/documents/${source.document_id}/download`}>
                                  Вимога {i + 1}: {source.locator} <ExternalLink size={12} />
                                </a>
                                <p>«{source.excerpt}»</p>
                              </div>
                            ))}
                          </details>
                        ))}
                        {!project.approved_hash && (
                          <div className="approval">
                            <p>
                              Перевірте вимоги, залежності й ручні кроки.
                              Погодження дозволить виконати цей план.
                            </p>
                            <button
                              className="primary"
                              disabled={
                                active ||
                                busy ||
                                project.plan.missing.length > 0 ||
                                project.plan.conflicts.length > 0
                              }
                              onClick={() => act("approve")}
                            >
                              <Check size={17} />
                              Погодити план
                            </button>
                          </div>
                        )}
                      </div>
                      <div className="step-list">
                        {project.steps.map((s: Data, i: number) => (
                          <StepCard
                            key={s.id}
                            step={s}
                            index={i}
                            busy={busy || active}
                            onManual={(text, confirmed) =>
                              run(async () => {
                                await api(
                                  `/projects/${selected}/steps/${s.id}/manual`,
                                  "POST",
                                  { text, confirmed },
                                );
                                notify("Результат передано на перевірку");
                              })
                            }
                          />
                        ))}
                      </div>
                      {project.plan.sources?.length > 0 && (
                        <div className="panel">
                          <h3>Джерела плану</h3>
                          {project.plan.sources.map((s: Data, i: number) => (
                            <div className="source" key={i}>
                              <a
                                href={`/api/documents/${s.document_id}/download`}
                              >
                                {s.locator}
                                <ExternalLink size={12} />
                              </a>
                              <p>«{s.excerpt}»</p>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="empty">
                      <Search size={30} />
                      <h3>Спочатку зрозуміємо завдання</h3>
                      <p>
                        Додайте методичку й зразок звіту у вкладці «Матеріали»,
                        потім натисніть «Проаналізувати».
                      </p>
                      <button
                        className="secondary"
                        onClick={() => setTab("materials")}
                      >
                        Додати матеріали <ArrowRight size={16} />
                      </button>
                      <button
                        className="text-button"
                        onClick={() => setModal("plan")}
                      >
                        Або скласти план вручну
                      </button>
                    </div>
                  )}
                </>
              )}
              {tab === "materials" && (
                <>
                  <ImportPanel
                    pid={selected}
                    subject={project.subject}
                    upload={uploadFiles}
                    busy={busy || active}
                    onImport={async (path, role, subject) => {
                      await api("/import", "POST", {
                        path,
                        role,
                        subject,
                        project_id: selected,
                      });
                      notify("Імпорт у черзі");
                    }}
                  />
                  <ProjectDocuments
                    pid={selected}
                    refresh={project.documents.length}
                  />
                  <button
                    className="secondary"
                    disabled={busy || active}
                    onClick={() => act("index")}
                  >
                    <Layers size={16} />
                    Побудувати семантичний індекс
                  </button>
                  <p className="muted">
                    Пошук за словами працює відразу. Семантичний індекс потребує
                    embeddinggemma в Ollama.
                  </p>
                </>
              )}
              {tab === "chat" && (
                <Chat
                  messages={project.messages}
                  busy={busy || active}
                  send={(text) =>
                    run(async () => {
                      await api(`/projects/${selected}/chat`, "POST", { text });
                      await load();
                    })
                  }
                  cloud={(text) =>
                    run(async () => {
                      const q = await api(
                        `/projects/${selected}/cloud/quote`,
                        "POST",
                        { text },
                      );
                      if (settings.cloud_consent_mode === "budgeted" && q.maximum_estimate_usd <= Number(settings.cloud_request_limit_usd)) {
                        await api(`/cloud/${q.id}/send-budgeted`, "POST");
                        notify("Запит надіслано в межах дозволеного бюджету.");
                      } else {
                        setQuote(q);
                      }
                    })
                  }
                />
              )}
              {tab === "files" && (
                <div className="panel">
                  <div className="panel-heading">
                    <h2>Файли та звіти</h2>
                    <button
                      className="secondary"
                      disabled={busy || active || project.status !== "completed"}
                      onClick={() => act("defense")}
                    >
                      <GraduationCap size={16} />
                      Підготовка до захисту
                    </button>
                    <button
                      className="secondary"
                      disabled={busy || active}
                      onClick={() => act("report")}
                    >
                      <FileText size={16} />
                      Створити новий звіт
                    </button>
                  </div>
                  <p className="muted">
                    Після завершення роботи автоматично готується окремий посібник до захисту: тема,
                    етапи, розбір коду, формули, результати, запитання з відповідями та усний виступ.
                    Для вже завершеної роботи скористайтеся кнопкою вище. Файли defense.md, DOCX і PDF з’являться після генерації.
                  </p>
                  <p className="muted">
                    Позначка «Виконано» підтверджує запуск. «Потребує перегляду»
                    означає, що результат ще слід перевірити.
                  </p>
                  {project.artifacts.length ? (
                    project.artifacts.map((a: Data) => (
                      <div className="artifact" key={a.id}>
                        <FileText size={20} />
                        <div>
                          <b>{a.name}</b>
                          <small>Версія роботи {a.revision}</small>
                        </div>
                        <Badge value={a.state} />
                        <a
                          className="icon-button"
                          href={`/api/projects/${selected}/artifacts/${a.id}`}
                          title="Завантажити"
                        >
                          <Download size={18} />
                        </a>
                      </div>
                    ))
                  ) : (
                    <div className="small-empty">
                      Результати з’являться після виконання кроків.
                    </div>
                  )}
                </div>
              )}
              {tab === "logs" && (
                <div className="panel">
                  <h2>Історія дій</h2>
                  {project.jobs.map((j: Data) => (
                    <div className="job" key={j.id}>
                      <Badge value={j.status} />
                      <b>{j.kind}</b>
                      <small>
                        {new Date(j.created).toLocaleString("uk-UA")}
                      </small>
                      {j.error && <pre>{j.error}</pre>}
                    </div>
                  ))}
                  {project.steps
                    .filter(
                      (s: Data) => Object.keys(s.result).length || s.error,
                    )
                    .map((s: Data) => (
                      <details className="log" key={s.id}>
                        <summary>{s.spec.title}</summary>
                        <pre>
                          {JSON.stringify(s.result, null, 2)}
                          {s.error}
                        </pre>
                      </details>
                    ))}
                </div>
              )}
            </>
          )}
          {screen !== "project" &&
            jobs.some((j) =>
              ["queued", "running", "failed"].includes(j.status),
            ) && (
              <details className="global-jobs">
                <summary>
                  <Loader2 size={15} />
                  Черга та останні дії
                </summary>
                {jobs.slice(0, 6).map((j) => (
                  <div className="job" key={j.id}>
                    <Badge value={j.status} />
                    <span>{j.kind}</span>
                    {j.error && <p>{j.error}</p>}
                  </div>
                ))}
              </details>
            )}
          <footer>
            Майстерня навчання<span>Створюйте. Перевіряйте. Розумійте.</span>
          </footer>
        </div>
      </main>
      {(modal === "create" || modal === "task") && (
        <Modal
          title={
            modal === "create" ? "Нова навчальна робота" : "Завдання роботи"
          }
          close={() => setModal("")}
        >
          <ProjectForm
            initial={modal === "task" ? project : undefined}
            busy={busy}
            save={(data) =>
              run(async () => {
                const p = await api(
                  modal === "task" ? "/projects/" + selected : "/projects",
                  modal === "task" ? "PATCH" : "POST",
                  data,
                );
                setModal("");
                open(p.id);
                await load();
              })
            }
          />
        </Modal>
      )}
      {modal === "plan" && project && (
        <Modal title="Редагування плану" close={() => setModal("")} wide>
          <PlanEditor
            initial={
              project.plan?.steps
                ? project.plan
                : {
                    goal: "",
                    requirements: [],
                    sources: [],
                    missing: [],
                    conflicts: [],
                    report_sections: [
                      "Мета",
                      "Хід роботи",
                      "Результати",
                      "Висновки",
                    ],
                    steps: [],
                  }
            }
            busy={busy}
            save={(plan) =>
              run(async () => {
                await api(`/projects/${selected}/plan`, "PUT", plan);
                setModal("");
                await load();
                notify("План оновлено. Погодьте його перед виконанням.");
              })
            }
          />
        </Modal>
      )}
      {quote && (
        <Modal
          title="Окремий дозвіл на хмарний запит"
          close={() => setQuote(null)}
        >
          <p>
            До <b>{quote.provider_name || "Google Gemini API"} · {quote.model}</b> буде передано наведений нижче текст. Цей
            дозвіл стосується одного запиту.
          </p>
          <div className="cost">
            <span>Максимальна оцінка</span>
            <b>${quote.maximum_estimate_usd.toFixed(5)}</b>
            <small>
              Використано ${quote.spent_usd.toFixed(4)} із ${quote.budget_usd}
            </small>
          </div>
          {quote.pricing_note && <p className="muted">{quote.pricing_note}</p>}
          <pre className="quote-preview">{quote.prompt}</pre>
          <button
            className="primary"
            disabled={busy}
            onClick={() =>
              run(async () => {
                await api(`/cloud/${quote.id}/send`, "POST");
                setQuote(null);
                notify("Дозволений запит додано до черги");
              })
            }
          >
            <Cloud size={17} />
            Дозволити цей запит
          </button>
        </Modal>
      )}
    </div>
  );
}

function Modal({
  title,
  close,
  children,
  wide = false,
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section
        className={"modal " + (wide ? "wide" : "")}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="panel-heading">
          <h2>{title}</h2>
          <button className="icon-button" onClick={close} aria-label="Закрити">
            <X size={20} />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}
function ProjectForm({
  initial,
  busy,
  save,
}: {
  initial?: Data | null;
  busy: boolean;
  save: (p: Data) => void;
}) {
  const [p, setP] = useState(
    initial
      ? {
          title: initial.title,
          subject: initial.subject,
          task: initial.task,
          variant: initial.variant,
        }
      : { title: "", subject: "", task: "", variant: "" },
  );
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save(p);
      }}
    >
      <Field label="Назва роботи">
        <input
          required
          value={p.title}
          onChange={(e) => setP({ ...p, title: e.target.value })}
          placeholder="Наприклад, Розрахункова робота № 1"
        />
      </Field>
      <div className="form-grid">
        <Field label="Предмет">
          <input
            value={p.subject}
            onChange={(e) => setP({ ...p, subject: e.target.value })}
            placeholder="Назва дисципліни"
          />
        </Field>
        <Field label="Варіант, якщо є">
          <input
            value={p.variant}
            onChange={(e) => setP({ ...p, variant: e.target.value })}
            placeholder="Необов’язково"
          />
        </Field>
      </div>
      <Field label="Що потрібно зробити">
        <textarea
          rows={7}
          required
          value={p.task}
          onChange={(e) => setP({ ...p, task: e.target.value })}
          placeholder="Вставте умову або опишіть роботу. Методичку й зразки можна додати наступним кроком."
        />
      </Field>
      <button className="primary" disabled={busy}>
        {busy ? (
          <Loader2 className="spin" size={17} />
        ) : (
          <ArrowRight size={17} />
        )}
        Зберегти роботу
      </button>
    </form>
  );
}
function SemesterPanel({busy, notify}: {busy: boolean; notify: (message: string) => void}) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const refresh = async () => {
    setLoading(true);
    try { setData(await api("/semester")); setError(""); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refresh(); }, []);
  return <div className="panel">
    <div className="panel-heading"><h2>Матеріали всього семестру</h2>
      <button className="secondary" disabled={busy || loading} onClick={refresh}>Перевірити папку</button>
    </div>
    <p>Скопіюйте файли в <code>{data?.root || "D:\\StudyAssistant\\materials\\semester"}</code>.
      Папки предметів визначають, до якої дисципліни належить матеріал; вкладені папки визначають його призначення.</p>
    <p className="muted">Все нерозсортоване можна тимчасово скласти в 00_UNSORTED. Перед імпортом його потрібно розподілити за предметами.
      Лекції — 01_Лекції, умови робіт — 02_Лабораторні, чужі зразки — 03_Зразки_звітів, датасети — 04_Дані.
      Імпорт копіює файли до бібліотеки, оригінали залишаються на місці. Пошук за змістом індексується окремим завданням після читання.</p>
    {error && <p role="alert">{error}</p>}
    {data && <>
      <p>Готові до імпорту: {data.files.length}. Нерозсортовані: {data.unassigned.length}. Непідтримувані: {data.unsupported.length}.</p>
      {Boolean(data.catalog_unmatched?.length) && <p role="status">Каталог містить {data.catalog_unmatched.length} записів без доступного файлу за вказаним шляхом. Перевірте розподіл перед імпортом: для перенесених файлів використовуються їхні поточні папки.</p>}
      <details><summary>Перевірити розподіл файлів</summary>
        {data.files.map((f: Data) => <p key={f.relative}><code>{f.relative}</code> — {f.subject} · {f.role} · {f.assignment_source === "catalog" ? "з каталогу" : "за папками"}</p>)}
        {data.catalog_unmatched?.map((f: string) => <p key={`catalog:${f}`}>Запис каталогу без доступного файлу: {f}</p>)}
        {data.unassigned.map((f: string) => <p key={f}>Потрібен предмет: {f}</p>)}
        {data.unsupported.map((f: string) => <p key={f}>Формат не читається: {f}</p>)}
      </details>
      <button className="primary" disabled={busy || loading || !data.files.length || Boolean(data.unassigned.length)} onClick={async () => {
        setLoading(true);
        try { await api("/semester/import", "POST"); notify("Імпорт семестру додано до черги. Читання й OCR можуть тривати довго."); }
        catch (e) { setError(String(e)); }
        finally { setLoading(false); }
      }}>Імпортувати семестр</button>
    </>}
  </div>;
}

function ImportPanel({
  pid,
  subject,
  upload,
  onImport,
  busy,
}: {
  pid: string;
  subject: string;
  upload: (
    files: FileList | null,
    role: string,
    subject: string,
    pid: string,
  ) => void;
  onImport: (path: string, role: string, subject: string) => Promise<void>;
  busy: boolean;
}) {
  const [role, setRole] = useState("theory"),
    [sub, setSub] = useState(subject),
    [path, setPath] = useState(""),
    [err, setErr] = useState("");
  return (
    <div className="panel import-panel">
      <div className="panel-heading">
        <h2>Додати матеріали</h2>
        <Upload size={20} />
      </div>
      <div className="form-grid">
        <Field label="Тип матеріалу">
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            {Object.entries(roles).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Предмет">
          <input
            value={sub}
            onChange={(e) => setSub(e.target.value)}
            placeholder="Папки верхнього рівня задають предмети"
          />
        </Field>
      </div>
      <label className="dropzone">
        <Upload size={23} />
        <b>{busy ? "Обробляємо матеріали…" : "Вибрати файли з комп’ютера"}</b>
        <span>PDF, Word, презентації, зображення, таблиці</span>
        <input
          type="file"
          multiple
          disabled={busy}
          accept=".pdf,.docx,.doc,.pptx,.ppt,.png,.jpg,.jpeg,.txt,.md,.csv,.xlsx,.json,.ipynb,.py,.js,.ts,.c,.cpp,.h,.java,.cs,.r,.m,.sql,.ino,.dwg,.dxf,.stl,.obj,.blend,.mat,.mp4,.avi,.wav,.zip"
          onChange={(e) => upload(e.target.files, role, sub, pid)}
        />
      </label>
      <div className="or">або підключіть локальну папку</div>
      <form
        className="inline-form"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            setErr("");
            await onImport(path, role, sub);
          } catch (e) {
            setErr((e as Error).message);
          }
        }}
      >
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="D:\Навчання\Матеріали"
          required
        />
        <button className="secondary" disabled={busy}>
          Імпортувати
        </button>
      </form>
      {err && <p className="error-text">{err}</p>}
      <p className="muted">
        Зразки звітів задають структуру й оформлення. Їхні результати не
        переносяться у вашу роботу.
      </p>
    </div>
  );
}
function DocumentList({ docs }: { docs: Data[] }) {
  const [reparsing, setReparsing] = useState("");
  const [message, setMessage] = useState("");
  return docs.length ? (
    <div className="document-list">
      {docs.map((d) => (
        <div className="document" key={d.id}>
          <div className="file-icon">
            <FileText size={19} />
          </div>
          <div>
            <a href={`/api/documents/${d.id}/download`}>{d.name}</a>
            <small>
              {d.subject} · {roles[d.role]} ·{" "}
              {d.indexed ? "Індексовано" : "Пошук за словами"}
            </small>
            {d.warnings !== "[]" && (
              <details>
                <summary>Примітки до читання</summary>
                <p>
                  {(() => {
                    try {
                      return JSON.parse(d.warnings).join("\n");
                    } catch {
                      return d.warnings;
                    }
                  })()}
                </p>
              </details>
            )}
          </div>
          <Badge value={d.status === "ready" ? "done" : "needs_review"} />
          {d.status === "needs_review" && (
            <button
              className="text-button"
              disabled={reparsing === d.id}
              onClick={async () => {
                setReparsing(d.id);
                try {
                  await api(`/documents/${d.id}/reparse`, "POST");
                  setMessage(
                    "Повторне читання додано до черги. Оновіть список після завершення.",
                  );
                } catch (e) {
                  setMessage((e as Error).message);
                } finally {
                  setReparsing("");
                }
              }}
            >
              Перечитати
            </button>
          )}
        </div>
      ))}
      {message && <p className="muted">{message}</p>}
    </div>
  ) : (
    <div className="small-empty">Матеріалів поки немає.</div>
  );
}
function ProjectDocuments({ pid, refresh }: { pid: string; refresh: number }) {
  const [docs, setDocs] = useState<Data[]>([]);
  const [selection, setSelection] = useState<Data | null>(null);
  const [sourceError, setSourceError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    api("/documents?pid=" + pid).then(setDocs);
    api(`/projects/${pid}/sources`).then(setSelection).catch(e => setSourceError(String(e)));
  }, [pid, refresh]);
  return (
    <div className="panel">
      <h2>Матеріали цієї роботи</h2>
      <details>
        <summary>Вибрати умову та додаткові файли з бібліотеки</summary>
        <p className="muted">Лекції предмета та файли, завантажені прямо в цю роботу, доступні завжди.
          Виберіть потрібну лабораторну, щоб не змішувати її з іншими роботами. Зміна вибору скасує погодження плану.</p>
        {sourceError && <p role="alert">{sourceError}</p>}
        {selection?.available.filter((d: Data) => d.role !== "theory").map((d: Data) => <label key={d.id} style={{display:"block"}}>
          <input type="checkbox" checked={selection.document_ids === null || selection.document_ids.includes(d.id)} onChange={(e) => {
            const current: string[] = selection.document_ids ?? selection.available.filter((x: Data) => x.role !== "theory").map((x: Data) => x.id);
            setSelection({...selection, document_ids: e.target.checked ? [...new Set([...current,d.id])] : current.filter(id => id !== d.id)});
          }} /> {d.name} · {d.role}
        </label>)}
        <button className="secondary" disabled={saving || !selection} onClick={async () => {
          setSaving(true);
          try {
            setSelection(await api(`/projects/${pid}/sources`, "PUT", {document_ids:selection?.document_ids}));
            setSourceError("Вибір збережено. Повторіть аналіз і погодьте новий план.");
          } catch (e) { setSourceError(String(e)); }
          finally { setSaving(false); }
        }}>Зберегти вибір матеріалів</button>
      </details>
      <DocumentList docs={docs} />
    </div>
  );
}
function StepCard({
  step,
  index,
  busy,
  onManual,
}: {
  step: Data;
  index: number;
  busy: boolean;
  onManual: (text: string, confirmed: boolean) => void;
}) {
  const [text, setText] = useState(""),
    [confirmed, setConfirmed] = useState(false);
  const [retryMessage, setRetryMessage] = useState("");
  const [cloudOptions, setCloudOptions] = useState<Data[]>([]);
  const [cloudRequest, setCloudRequest] = useState("");
  return (
    <div className={"step-card " + step.status}>
      <div className="step-number">
        {step.status === "done" ? (
          <Check size={19} />
        ) : step.status === "running" ? (
          <Loader2 size={19} className="spin" />
        ) : (
          String(index + 1).padStart(2, "0")
        )}
      </div>
      <div className="step-body">
        <div className="step-heading">
          <h3>{step.spec.title}</h3>
          <Badge value={step.status} />
        </div>
        <span className="step-type">
          {kinds[step.spec.kind]}
          {step.spec.kind === "code" ? " · " + step.spec.language : ""}
        </span>
        <p>{step.spec.instruction}</p>
        {step.spec.urls?.map((url: string) => (
          <p key={url} className="muted">
            Погоджене посилання: {url}
          </p>
        ))}
        <div className="expected">
          <CheckCircle2 size={15} />
          <span>{step.spec.expected}</span>
        </div>
        {step.spec.dependencies?.length > 0 && (
          <p className="muted">
            Залежності: {step.spec.dependencies.join(", ")}
          </p>
        )}
        {step.error && ["failed", "blocked", "waiting_user"].includes(step.status) && (
          <details className="error-text">
            <summary>Причина зупинки</summary>
            <pre>{step.error}</pre>
          </details>
        )}
        {["failed", "blocked"].includes(step.status) && (
          <>
            <button
              className="secondary"
              disabled={busy}
              onClick={async () => {
                try {
                  const r = await api(
                    `/projects/${step.project_id}/steps/${step.id}/retry`,
                    "POST",
                  );
                  setRetryMessage(r.message);
                } catch (e) {
                  setRetryMessage((e as Error).message);
                }
              }}
            >
              <RefreshCw size={14} />
              Підготувати повтор
            </button>
            {retryMessage && <p className="muted">{retryMessage}</p>}
            {step.spec.kind === "code" && <>
              <button className="text-button" disabled={busy} onClick={async () => {
                try {
                  const options = await api(`/projects/${step.project_id}/cloud/results`);
                  setCloudOptions(options);
                  setCloudRequest(options[0]?.id || "");
                  if (!options.length) setRetryMessage("Завершених хмарних відповідей із кодом ще немає. Новий API-запит потребує окремого дозволу.");
                } catch (error) { setRetryMessage((error as Error).message); }
              }}>Використати вже отриманий код із хмари</button>
              {cloudOptions.length > 0 && <div className="manual-form">
                <Field label="Завершений погоджений запит">
                  <select value={cloudRequest} onChange={(e) => setCloudRequest(e.target.value)}>
                    {cloudOptions.map((q) => <option key={q.id} value={q.id}>{q.model} · {q.purpose}</option>)}
                  </select>
                </Field>
                <button className="secondary" disabled={busy || !cloudRequest} onClick={async () => {
                  try {
                    const result = await api(`/projects/${step.project_id}/steps/${step.id}/cloud/${cloudRequest}/apply`, "POST");
                    setRetryMessage(result.message);
                    setCloudOptions([]);
                  } catch (error) { setRetryMessage((error as Error).message); }
                }}>Підготувати код до локального запуску</button>
                <p className="muted">Це використовує збережену відповідь. Нового API-запиту немає.</p>
              </div>}
            </>}
          </>
        )}
        {step.result?.instructions && <Md text={step.result.instructions} />}{" "}
        {step.status === 'waiting_user' && step.spec.kind === 'code' && step.result?.exit_code === 0 && !step.result?.review_only_attempts && <>
          <button className="secondary" disabled={busy} onClick={async () => {
            try {
              await api(`/projects/${step.project_id}/steps/${step.id}/reassess`, 'POST');
              setRetryMessage('Переглядаю збережені результати й перевірки. Код повторно не виконується.');
            } catch (error) { setRetryMessage((error as Error).message); }
          }}><RefreshCw size={14} />Повторно перевірити результат</button>
          {retryMessage && <p className="muted">{retryMessage}</p>}
        </>}
        {step.result?.assessment_recovery?.available && <>
          <button className="secondary" disabled={busy} onClick={async () => {
            try {
              await api(`/projects/${step.project_id}/steps/${step.id}/recover-assessment`, 'POST');
              setRetryMessage('Оцінюю збережений результат після збою сервісу. Код повторно не виконується.');
            } catch (error) { setRetryMessage((error as Error).message); }
          }}><RefreshCw size={14} />Відновити оцінку після збою сервісу</button>
          <p className="muted">Нова оцінка використовує чинний API-бюджет. Попередній невизначений запит і його резерв зберігаються.</p>
          {retryMessage && <p className="muted">{retryMessage}</p>}
        </>}
        {step.status === "waiting_user" && (
          <form
            className="manual-form"
            onSubmit={(e) => {
              e.preventDefault();
              onManual(text, confirmed);
            }}
          >
            <Field label="Ваш результат">
              <textarea
                rows={4}
                required
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Опишіть результат. Файли й скриншоти додайте у Матеріали → Мій результат."
              />
            </Field>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              Підтверджую самостійно, без автоматичної перевірки
            </label>
            <button className="secondary" disabled={busy}>
              <Send size={15} />
              Передати результат
            </button>
          </form>
        )}
        {step.status === "done" && step.result?.evidence && (
          <small className="muted">{step.result.evidence}</small>
        )}
      </div>
    </div>
  );
}
function Chat({
  messages,
  busy,
  send,
  cloud,
}: {
  messages: Data[];
  busy: boolean;
  send: (t: string) => void;
  cloud: (t: string) => void;
}) {
  const [text, setText] = useState("");
  return (
    <div className="chat">
      <div className="chat-messages">
        {messages.length ? (
          messages.map((m) => (
            <div key={m.id} className={"message " + m.role}>
              <div className="message-author">
                {m.role === "user" ? "Ви" : "Помічник"}
              </div>
              <Md text={m.content} />
            </div>
          ))
        ) : (
          <div className="small-empty">
            Поставте запитання, уточніть умову або попросіть пояснити результат.
          </div>
        )}
      </div>
      <form
        className="chat-compose"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
          setText("");
        }}
      >
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Запитайте або уточніть завдання…"
          rows={3}
        />
        <div>
          <span className="muted">Зміни до виконання погоджуються у плані</span>
          <button
            type="button"
            className="secondary"
            disabled={busy || !text.trim()}
            onClick={() => cloud(text)}
          >
            <Cloud size={16} />
            Залучити хмару
          </button>
          <button className="primary" disabled={busy || !text.trim()}>
            <Send size={17} />
            Надіслати
          </button>
        </div>
      </form>
    </div>
  );
}
function PlanEditor({
  initial,
  busy,
  save,
}: {
  initial: Data;
  busy: boolean;
  save: (p: Data) => void;
}) {
  const [p, setP] = useState<Data>(structuredClone(initial));
  const list = (key: string, value: string) =>
    setP({ ...p, [key]: value.split("\n").filter(Boolean) });
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save(p);
      }}
    >
      <Field label="Мета">
        <textarea
          value={p.goal}
          onChange={(e) => setP({ ...p, goal: e.target.value })}
        />
      </Field>
      <Field label="Вимоги — по одній на рядок">
        <textarea
          value={p.requirements.join("\n")}
          onChange={(e) => list("requirements", e.target.value)}
        />
      </Field>
      <div className="form-grid">
        <Field label="Відсутні дані — очистіть після уточнення">
          <textarea
            value={p.missing.join("\n")}
            onChange={(e) => list("missing", e.target.value)}
          />
        </Field>
        <Field label="Суперечності — очистіть після рішення">
          <textarea
            value={p.conflicts.join("\n")}
            onChange={(e) => list("conflicts", e.target.value)}
          />
        </Field>
      </div>
      <Field label="Зауваження, що не блокують виконання">
        <textarea
          value={(p.warnings || []).join("\n")}
          onChange={(e) => list("warnings", e.target.value)}
        />
      </Field>
      <Field label="Розділи звіту — по одному на рядок">
        <textarea
          value={p.report_sections.join("\n")}
          onChange={(e) => list("report_sections", e.target.value)}
        />
      </Field>
      {p.steps.map((s: Data, i: number) => (
        <div className="edit-step" key={i}>
          <div className="panel-heading">
            <b>Крок {i + 1}</b>
            <button
              type="button"
              className="text-button"
              onClick={() =>
                setP({
                  ...p,
                  steps: p.steps.filter((_: any, j: number) => i !== j),
                })
              }
            >
              Видалити
            </button>
          </div>
          {p.artifact_contract_version === 1 && <div className="form-grid">
            {["artifact_inputs", "artifact_outputs"].map((key) => <Field key={key} label={key === "artifact_inputs" ? "Читає файли/теки — відносні шляхи, по одному на рядок" : "Передає наступним крокам — відносні шляхи, по одному на рядок"}>
              <textarea value={(s[key] || []).join("\n")} onChange={(e) => {
                const steps = [...p.steps];
                steps[i] = {...s, [key]: e.target.value.split("\n").map((line) => line.trim()).filter(Boolean)};
                setP({...p, steps});
              }} />
            </Field>)}
          </div>}
          <Field label="Назва">
            <input
              value={s.title}
              onChange={(e) => {
                const steps = [...p.steps];
                steps[i] = { ...s, title: e.target.value };
                setP({ ...p, steps });
              }}
            />
          </Field>
          <div className="form-grid">
            {["kind", "language"].map((key) => (
              <Field label={key === "kind" ? "Тип дії" : "Мова коду"} key={key}>
                <select
                  value={s[key]}
                  onChange={(e) => {
                    const steps = [...p.steps];
                    steps[i] = { ...s, [key]: e.target.value };
                    setP({ ...p, steps });
                  }}
                >
                  {(key === "kind" ? Object.keys(kinds) : languages).map(
                    (k) => (
                      <option key={k} value={k}>
                        {kinds[k] || k}
                      </option>
                    ),
                  )}
                </select>
              </Field>
            ))}
          </div>
          {[
            ["instruction", "Інструкція"],
            ["expected", "Критерій перевірки"],
          ].map(([key, label]) => (
            <Field label={label} key={key}>
              <textarea
                value={s[key]}
                onChange={(e) => {
                  const steps = [...p.steps];
                  steps[i] = { ...s, [key]: e.target.value };
                  setP({ ...p, steps });
                }}
              />
            </Field>
          ))}
          <div className="form-grid">
            {s.kind === "fetch" && (
              <Field label="Погоджені HTTPS-посилання — по одному на рядок">
                <textarea
                  value={(s.urls || []).join("\n")}
                  onChange={(e) => {
                    const steps = [...p.steps];
                    steps[i] = {
                      ...s,
                      urls: e.target.value.split("\n").filter(Boolean),
                    };
                    setP({ ...p, steps });
                  }}
                />
              </Field>
            )}
            {s.kind === "fetch" && <Field label="Максимум завантаження, МБ">
              <input type="number" min={0.000001} max={3000}
                value={(s.max_download_bytes ?? 50000000) / 1000000}
                onChange={(e) => {
                  const steps = [...p.steps];
                  steps[i] = {...s,max_download_bytes:Math.round(Number(e.target.value)*1000000)};
                  setP({...p,steps});
                }} />
            </Field>}
            <Field label="Пакети — через кому">
              <input
                value={s.dependencies.join(", ")}
                onChange={(e) => {
                  const steps = [...p.steps];
                  steps[i] = {
                    ...s,
                    dependencies: e.target.value
                      .split(",")
                      .map((x) => x.trim())
                      .filter(Boolean),
                  };
                  setP({ ...p, steps });
                }}
              />
            </Field>
            <Field label="Ліміт одного запуску, секунд">
              <input
                type="number"
                min={1}
                max={7200}
                value={s.timeout_seconds}
                onChange={(e) => {
                  const steps = [...p.steps];
                  steps[i] = { ...s, timeout_seconds: Number(e.target.value) };
                  setP({ ...p, steps });
                }}
              />
            </Field>
          </div>
        </div>
      ))}
      <div className="toolbar">
        <button
          type="button"
          className="secondary"
          onClick={() =>
            setP({
              ...p,
              steps: [
                ...p.steps,
                {
                  title: "Новий крок",
                  kind: "write",
                  language: "python",
                  instruction: "",
                  expected: "",
                  dependencies: [],
                  timeout_seconds: 600,
                },
              ],
            })
          }
        >
          <Plus size={16} />
          Додати крок
        </button>
        <button className="primary" disabled={busy}>
          Зберегти зміни
        </button>
      </div>
      <p className="muted">
        Зміна плану скидає погодження та позначає попередні результати
        застарілими.
      </p>
    </form>
  );
}
function SettingsView({
  settings: s,
  setSettings: set,
  health: h,
  busy,
  save,
  diagnose,
  pull,
  backup,
}: {
  settings: Data;
  setSettings: (s: Data) => void;
  health: Data | null;
  busy: boolean;
  save: () => void;
  diagnose: () => void;
  pull: (m: string) => void;
  backup: () => void;
}) {
  const [maintenance, setMaintenance] = useState("");
  return (
    <>
      <div className="panel">
        <div className="panel-heading">
          <h2>Стан ноутбука</h2>
          <button className="secondary" onClick={diagnose} disabled={busy}>
            <RefreshCw size={15} />
            Перевірити
          </button>
        </div>
        {h ? (
          <>
            <div className="health-grid">
              <div>
                <span
                  className={
                    "dot " + (h.ollama.available ? "completed" : "error")
                  }
                />
                <b>Ollama</b>
                <small>
                  {h.ollama.available ? "Працює" : "Потрібно запустити"}
                </small>
              </div>
              <div>
                <span className={"dot " + (h.docker ? "completed" : "error")} />
                <b>Docker</b>
                <small>{h.docker ? "Працює" : "Потрібно запустити"}</small>
              </div>
              <div>
                <b>{h.ram.available_gb} ГБ</b>
                <small>Доступна RAM із {h.ram.total_gb}</small>
              </div>
              <div>
                <b>{h.disks["D:/"]} ГБ</b>
                <small>Вільно на D:</small>
              </div>
            </div>
            {h.disks["C:/"] < 10 && (
              <div className="alert warning">
                <AlertCircle size={19} />
                На C: вільно {h.disks["C:/"]} ГБ. Перед установленням моделей і
                контейнерів потрібно щонайменше 10 ГБ. Ваші файли автоматично не
                видаляються.
              </div>
            )}
            <p className="muted">
              {h.gpu} · Python {h.python}
            </p>
            <p className="muted">Дані: {h.data_dir}</p>
            <details>
              <summary>Службові компоненти</summary>
              {Object.entries(h.tools).map(([k, v]) => (
                <p key={k}>
                  {k}: {String(v || "не знайдено")}
                </p>
              ))}
              <p>
                Запуск середовища: scripts\setup.ps1. Налаштування та запуск:
                start.cmd.
              </p>
            </details>
          </>
        ) : (
          <p>Перевіряємо середовище…</p>
        )}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        <div className="panel">
          <h2>Локальні моделі</h2>
          <div className="form-grid">
            {[
              ["ollama_url", "Адреса Ollama"],
              ["model", "Модель помічника"],
              ["coding_model", "Модель коду (порожньо — основна модель)"],
              ["embedding_model", "Модель пошуку"],
            ].map(([k, l]) => (
              <Field key={k} label={l}>
                <input
                  value={s[k] ?? ""}
                  onChange={(e) => set({ ...s, [k]: e.target.value })}
                />
              </Field>
            ))}
            <Field label="Контекст">
              <select
                value={s.context || 4096}
                onChange={(e) => set({ ...s, context: Number(e.target.value) })}
              >
                <option value={4096}>4096 — початковий режим</option>
                <option value={8192}>8192 — потребує більше пам’яті</option>
              </select>
            </Field>
          </div>
          <div className="toolbar">
            <button
              type="button"
              className="secondary"
              onClick={() => pull(s.model)}
              disabled={busy}
            >
              <Download size={16} />
              Завантажити модель
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => pull(s.embedding_model)}
              disabled={busy}
            >
              Завантажити модель пошуку
            </button>
            {s.coding_model && <button type="button" className="secondary" onClick={() => pull(s.coding_model)} disabled={busy}><Download size={16} />Завантажити модель коду</button>}
          </div>
        </div>
        <div className="panel">
          <h2>Оформлення та особисті дані</h2>
          <label><input type="checkbox" checked={Boolean(s.auto_defense)} onChange={(e) => set({...s, auto_defense:e.target.checked})} /> Автоматично готувати до захисту після завершення роботи</label>
          <div className="form-grid">
            {[
              ["author", "Ім’я для титульної сторінки"],
              ["group", "Група"],
              ["institution", "Навчальний заклад"],
              ["library_root", "Коренева папка матеріалів"],
              ["semester_root", "Папка матеріалів семестру"],
            ].map(([k, l]) => (
              <Field key={k} label={l}>
                <input
                  value={s[k] ?? ""}
                  onChange={(e) => set({ ...s, [k]: e.target.value })}
                />
              </Field>
            ))}
          </div>
        </div>
        <div className="panel">
          <h2>Хмарне підсилення</h2>
          <p className="muted">
            {s.cloud_consent_mode === "budgeted" ? "Потрібні звернення дозволені в межах заданих лімітів." : "Кожне звернення потребує окремого дозволу."} Локальна оцінка разом із резервами: $
            {Number(s.spent_usd || 0).toFixed(5)} цього місяця.
          </p>
          <p className="muted">Фактичні списання та залишок коштів перевіряйте в кабінеті провайдера. Це облік лише викликів цієї програми.</p>
          {s.cloud_consent_mode === "budgeted" && (
            <p className="muted">Помічник може сам залучати хмарну модель для виправлення плану, помилок коду й перевірки звіту. Мету та вартість кожного звернення збережено в історії.</p>
          )}
          <div className="form-grid">
            <Field label="Дозвіл на хмарні звернення">
              <select value={s.cloud_consent_mode || "per_request"} onChange={(e) => set({...s, cloud_consent_mode:e.target.value})}>
                <option value="per_request">Погоджувати кожен запит</option>
                <option value="budgeted">Дозволяти в межах бюджету й ліміту запиту</option>
              </select>
            </Field>
            <Field label="Ліміт одного звернення за загальним дозволом, $">
              <input type="number" min={0} step="0.001" value={s.cloud_request_limit_usd ?? 0.03} onChange={(e) => set({...s, cloud_request_limit_usd:Number(e.target.value)})} />
            </Field>
            <Field label="Хмарний провайдер">
              <select value={s.cloud_provider || "gemini"} onChange={(e) => {
                const deepseek = e.target.value === "deepseek";
                set({ ...s, cloud_provider: e.target.value, cloud_api_key: undefined,
                  has_cloud_key: false,
                  cloud_model: deepseek ? "deepseek-flash" : "gemini-3.1-flash-lite",
                  cloud_input_price: deepseek ? 0.3 : 0.25,
                  cloud_output_price: deepseek ? 1.2 : 1.5,
                  cloud_cache_input_price: deepseek ? 0.006 : null,
                  cloud_deepseek_offpeak: deepseek,
                  cloud_prices_date: "2026-10-22",
                });
              }}>
                <option value="deepseek">DeepSeek API</option>
                <option value="gemini">Google Gemini API</option>
              </select>
            </Field>
            {s.cloud_provider === "deepseek" ? (
              <div>
                <p>Ключ: <code>DEEPSEEK_API_KEY</code> у <code>D:\StudyAssistant\.env</code>.</p>
                <p className="muted">{s.has_cloud_key ? "Ключ знайдено локально. API не перевірявся." : "Додайте ключ у .env та оновіть налаштування."} Сам ключ у браузер не передається.</p>
                <p className="muted">Для бюджету використовуються пікові тарифи без знижок кешу. Автоматичні запити можливі лише після вибору режиму погодженого бюджету.</p>
                <label><input type="checkbox" checked={Boolean(s.cloud_thinking)} onChange={(e) => set({...s, cloud_thinking:e.target.checked})} /> Режим міркування: також витрачає ліміт вихідних токенів</label>
              </div>
            ) : (
            <Field
              label={
                s.has_cloud_key
                  ? "API-ключ збережено; введіть новий для заміни"
                  : "API-ключ Gemini"
              }
            >
              <input
                type="password"
                autoComplete="new-password"
                value={s.cloud_api_key ?? ""}
                onChange={(e) => set({ ...s, cloud_api_key: e.target.value })}
              />
            </Field>
            )}
            {[
              ["cloud_model", "Модель"],
              ["cloud_budget", "Місячний бюджет, $"],
              ["cloud_max_output", "Ліміт вихідних токенів, включно з міркуванням"],
              ["cloud_input_price", "Максимальна ціна входу без кешу за 1 млн токенів, $"],
              ["cloud_output_price", "Максимальна ціна виходу за 1 млн токенів, $"],
              ["cloud_prices_date", "Дата перевірки тарифів"],
            ].map(([k, l]) => (
              <Field key={k} label={l}>
                <input
                  value={s[k] ?? ""}
                  type={
                    k === "cloud_prices_date"
                      ? "date"
                      : k === "cloud_model"
                        ? "text"
                        : "number"
                  }
                  step="any"
                  min={0}
                  onChange={(e) =>
                    set({
                      ...s,
                      [k]:
                        (k.includes("price") && k !== "cloud_prices_date") ||
                        k === "cloud_budget" || k === "cloud_max_output"
                          ? Number(e.target.value)
                          : e.target.value,
                    })
                  }
                />
              </Field>
            ))}
            {s.cloud_provider === "deepseek" && <>
              <Field label="Ціна кешованого входу за 1 млн токенів у піковий час, $">
                <input type="number" min={0} step="any" value={s.cloud_cache_input_price ?? ""} onChange={e=>set({...s,cloud_cache_input_price:e.target.value===""?null:Number(e.target.value)})} />
              </Field>
              <Field label="Знижка DeepSeek Flash поза піковим часом">
                <input type="checkbox" checked={!!s.cloud_deepseek_offpeak} onChange={e=>set({...s,cloud_deepseek_offpeak:e.target.checked})} />
                <span className="muted">50% за розкладом UTC. Винятки для китайських свят автоматично не визначаються. Резерв запиту рахується за максимальним тарифом.</span>
              </Field>
            </>}
          </div>
          <a
            href={s.cloud_provider === "deepseek" ? "https://api-docs.deepseek.com/quick_start/pricing/" : "https://ai.google.dev/gemini-api/docs/pricing"}
            target="_blank"
            rel="noreferrer"
          >
            Перевірити офіційні тарифи <ExternalLink size={13} />
          </a>
        </div>
        <div className="panel">
          <h2>Ресурси та інструменти</h2>
          <div className="form-grid">
            {[
              ["container_memory_mb", "Пам’ять контейнера, МБ"],
              ["container_cpus", "Кількість CPU"],
              ["container_output_mb", "Ліміт файлів одного запуску, МБ"],
              ["poppler", "Шлях до pdftoppm.exe"],
              ["tesseract", "Шлях до tesseract.exe"],
              ["soffice", "Шлях до soffice.exe"],
            ].map(([k, l]) => (
              <Field label={l} key={k}>
                <input
                  value={s[k] ?? ""}
                  onChange={(e) =>
                    set({
                      ...s,
                      [k]: k.startsWith("container")
                        ? Number(e.target.value)
                        : e.target.value,
                    })
                  }
                />
              </Field>
            ))}
          </div>
        </div>
        <div className="toolbar">
          <button className="primary" disabled={busy}>
            <Check size={17} />
            Зберегти налаштування
          </button>
          <button
            type="button"
            className="secondary"
            onClick={backup}
            disabled={busy}
          >
            <Download size={17} />
            Резервна копія
          </button>
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={async () => {
              try {
                const r = await api("/cache/clear", "POST");
                setMaintenance(
                  r.message + " Звільнено " + r.reclaimed_mb + " МБ.",
                );
              } catch (e) {
                setMaintenance((e as Error).message);
              }
            }}
          >
            Очистити кеш завантажень
          </button>
        </div>
        {maintenance && <p className="muted">{maintenance}</p>}
      </form>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
