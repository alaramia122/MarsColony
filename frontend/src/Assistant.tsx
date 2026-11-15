import { useCallback, useEffect, useState } from "react";

type AIProposal = {
  id: string;
  author_player_id: string;
  status: string;
  version: number;
  proposal: {
    title: string; description: string; project_type: string; domain_codes: string[];
    budget_credits: number; duration_periods: number; labor_fte: number;
    energy_kwh_per_period: number; resource_requirements: Record<string, number>;
    expected_result: Record<string, unknown>; rationale: string;
    assumptions: string[]; risks: string[]; questions: string[];
  };
};
type AIEvent = {
  id: string; status: string; period_created: number;
  proposal: {
    title: string; category: string; severity: string; likelihood: number; trigger: string;
    narrative: string; player_options: string[]; consequences: string[];
    assumptions: string[];
  };
};
type KnowledgeEntity = {
  id: string; entity_key: string; entity_type: string; name: string;
  description: string; domain_codes: string[]; definition: Record<string, unknown>;
  assumptions: string[]; risks: string[]; version: number; status: string;
};

type Briefing = {
  headline: string; situation: string; key_changes: string[];
  risks: string[]; decisions_needed: string[]; next_period_focus: string[];
};

const localizeError = (message: string) => {
  const rules: Array<[RegExp, string]> = [
    [/AI provider is disabled|no AI provider is configured|provider.*not configured/i, "ИИ-провайдер не подключён. Настройте его в файле .env и перезапустите контейнер api."],
    [/API key.*(missing|required)|credentials.*missing/i, "Не задан API-ключ ИИ. Проверьте настройки провайдера в файле .env."],
    [/folder.*(missing|required)|folder id/i, "Не указан идентификатор каталога Yandex Cloud. Проверьте MARS_AI_YANDEX_FOLDER_ID."],
    [/Invalid player token/i, "Токен игрока недействителен. Войдите в игровую сессию заново."],
    [/Player token is required/i, "Для выполнения действия требуется авторизация игрока."],
    [/Game session not found/i, "Игровая сессия не найдена."],
    [/Only the colony commander/i, "Это действие доступно только командиру колонии."],
    [/AI request limit for this session/i, "Достигнут часовой лимит запросов к ИИ для игровой сессии."],
    [/AI request limit for this player/i, "Достигнут часовой лимит запросов к ИИ для этого игрока."],
    [/AI event did not match its response contract/i, "ИИ вернул событие в неподдерживаемом формате. Повторите запрос позже."],
    [/Game state changed while the event was being generated/i, "Состояние игры изменилось во время подготовки события. Обновите данные и повторите запрос."]
  ];
  for (const [pattern, replacement] of rules) if (pattern.test(message)) return replacement;
  return message;
};

async function aiRequest<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  headers.set("X-Player-Token", token);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(localizeError(body.detail || `Ошибка запроса: HTTP ${response.status}`));
  }
  return response.json() as Promise<T>;
}

const API = "/api/v1";
const money = (value: number) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value);
const domainNames: Record<string, string> = {
  geology: "Геология", industry: "Промышленность", energy: "Энергетика",
  life_support: "Жизнеобеспечение", construction: "Строительство",
  agriculture: "Агрокомплекс", medicine: "Медицина", social: "Социальное развитие",
  education: "Образование", science: "Наука", planetary: "Планетология",
  transport: "Транспорт", administration: "Администрация", safety: "Безопасность",
  communications: "Связь", external_relations: "Внешние связи"
};

export default function Assistant({ sessionId, token, playerId, eventWatermark, isCommander, canProposeEntity, onRefresh }: {
  sessionId: string; token: string; playerId: string; eventWatermark: number;
  isCommander: boolean; canProposeEntity: boolean;
  onRefresh: () => Promise<void>;
}) {
  const [requestText, setRequestText] = useState("");
  const [clarifications, setClarifications] = useState<Record<string, string>>({});
  const [eventFocus, setEventFocus] = useState("");
  const [entityText, setEntityText] = useState("");
  const [entities, setEntities] = useState<KnowledgeEntity[]>([]);
  const [proposals, setProposals] = useState<AIProposal[]>([]);
  const [events, setEvents] = useState<AIEvent[]>([]);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    const [projectData, eventData, entityData] = await Promise.all([
      aiRequest<{ proposals: AIProposal[] }>(`${API}/sessions/${sessionId}/ai/proposals`, token),
      aiRequest<{ events: AIEvent[] }>(`${API}/sessions/${sessionId}/ai/events`, token),
      aiRequest<{ entities: KnowledgeEntity[] }>(`${API}/sessions/${sessionId}/ai/entities`, token)
    ]);
    setProposals(projectData.proposals);
    setEvents(eventData.events);
    setEntities(entityData.entities);
  }, [sessionId, token]);

  useEffect(() => {
    void load().catch((reason: Error) => setError(localizeError(reason.message)));
  }, [load, eventWatermark]);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key); setError(""); setNotice("");
    try { await action(); }
    catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  };

  const generateBriefing = () => run("briefing", async () => {
    const result = await aiRequest<{ period: number; briefing: Briefing }>(
      `${API}/sessions/${sessionId}/ai/briefing`, token, { method: "POST" }
    );
    setBriefing(result.briefing);
    setNotice(`Сводка сформирована по состоянию колонии на период ${result.period}.`);
  });

  const generateProposal = () => run("proposal", async () => {
    const result = await aiRequest<AIProposal>(
      `${API}/sessions/${sessionId}/ai/proposals`, token,
      { method: "POST", body: JSON.stringify({ text: requestText }) }
    );
    setProposals((current) => [result, ...current]);
    setRequestText("");
    setNotice("Предложение сохранено. Проверьте расчёты и допущения перед созданием проекта.");
  });

  const proposalAction = (proposal: AIProposal, action: "create-project" | "reject") => run(
    `${proposal.id}-${action}`, async () => {
      await aiRequest(
        `${API}/sessions/${sessionId}/ai/proposals/${proposal.id}/${action}`,
        token, { method: "POST" }
      );
      await load();
      if (action === "create-project") {
        await onRefresh();
        setNotice("Создан черновик проекта. Для запуска он должен пройти обычное согласование.");
      } else setNotice("Предложение отклонено.");
    }
  );


  const clarifyProposal = (proposal: AIProposal) => run(
    `${proposal.id}-clarify`, async () => {
      const text = (clarifications[proposal.id] || "").trim();
      if (text.length < 2) throw new Error("Добавьте уточнение длиной не менее двух символов.");
      await aiRequest(
        `${API}/sessions/${sessionId}/ai/proposals/${proposal.id}/clarify`,
        token, { method: "POST", body: JSON.stringify({ text }) }
      );
      setClarifications((current) => ({ ...current, [proposal.id]: "" }));
      await load();
      setNotice("Предложение обновлено. Предыдущая версия и история уточнений сохранены.");
    }
  );


  const generateEntity = () => run("entity", async () => {
    const result = await aiRequest<KnowledgeEntity>(
      `${API}/sessions/${sessionId}/ai/entities`, token,
      { method: "POST", body: JSON.stringify({ text: entityText }) }
    );
    setEntities((current) => [result, ...current]);
    setEntityText("");
    setNotice("Сущность добавлена в очередь экспертной проверки. Игровая экономика не меняется.");
  });

  const entityAction = (entity: KnowledgeEntity, action: "approve" | "reject") => run(
    `${entity.id}-${action}`, async () => {
      await aiRequest(
        `${API}/sessions/${sessionId}/ai/entities/${entity.id}/${action}`,
        token, { method: "POST" }
      );
      await load();
      setNotice(action === "approve" ? "Версия сущности утверждена; предыдущая активная версия сохранена в истории." : "Предложение сущности отклонено.");
    }
  );

  const generateEvent = () => run("event", async () => {
    const result = await aiRequest<AIEvent>(
      `${API}/sessions/${sessionId}/ai/events`, token,
      { method: "POST", body: JSON.stringify({ focus: eventFocus }) }
    );
    setEvents((current) => [result, ...current]);
    setEventFocus("");
    setNotice("Событие подготовлено и ожидает решения командира.");
  });

  const eventAction = (event: AIEvent, action: "publish" | "reject") => run(
    `${event.id}-${action}`, async () => {
      await aiRequest(
        `${API}/sessions/${sessionId}/ai/events/${event.id}/${action}`,
        token, { method: "POST" }
      );
      await load();
      setNotice(action === "publish" ? "Событие опубликовано в общем журнале сессии." : "Событие отклонено.");
    }
  );

  return <div className="assistant-layout">
    <section className="panel assistant-panel">
      <div className="panel-heading">
        <div><div className="eyebrow">ИИ-ВЕДУЩИЙ</div><h2>Оперативная сводка</h2></div>
        <button className="button button-secondary small-button" disabled={Boolean(busy)} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : undefined} onClick={() => void generateBriefing()}>
          {busy === "briefing" ? "Анализ…" : "Сформировать сводку"}
        </button>
      </div>
      {briefing ? <>
        <h3 className="briefing-headline">{briefing.headline}</h3>
        <p className="briefing-situation">{briefing.situation}</p>
        <div className="briefing-columns">
          <BriefingList title="Изменения" items={briefing.key_changes} />
          <BriefingList title="Риски" items={briefing.risks} />
          <BriefingList title="Требуют решения" items={briefing.decisions_needed} />
          <BriefingList title="Фокус следующего периода" items={briefing.next_period_focus} />
        </div>
      </> : <p className="muted assistant-empty">Сводка строится по ресурсам, инфраструктуре, проектам и последним событиям текущей сессии.</p>}
    </section>

    <section className="panel assistant-panel">
      <div className="panel-heading"><div><div className="eyebrow">ЕСТЕСТВЕННЫЙ ЯЗЫК / ПРОЕКТНЫЙ ОФИС</div><h2>Предложить проект</h2></div></div>
      <p className="muted assistant-help">Опишите задачу обычным языком. ИИ подготовит проектную инициативу и отметит неизвестные параметры.</p>
      <textarea className="assistant-input" value={requestText} onChange={(event) => setRequestText(event.target.value)}
        placeholder="Например: организовать резервную линию производства кислорода, чтобы повысить устойчивость жизнеобеспечения…" rows={4} maxLength={8000} />
      <div className="assistant-toolbar"><span className="muted small">{requestText.length}/8000 · Предложение не меняет состояние игры</span>
        <button className="button button-primary" disabled={Boolean(busy) || requestText.trim().length < 5} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : "Введите описание не короче пяти символов."}
          onClick={() => void generateProposal()}>{busy === "proposal" ? "Формирование…" : "Сформировать предложение"}</button>
      </div>
    </section>

    <section className="panel assistant-panel">
      <div className="panel-heading"><div><div className="eyebrow">ПРОВЕРКА ПРЕДЛОЖЕНИЙ</div><h2>Проектные предложения</h2></div><span className="count-chip">{proposals.length}</span></div>
      {proposals.length === 0 ? <p className="muted assistant-empty">Здесь будут появляться предложения, сформированные игроками.</p> :
        <div className="assistant-cards">{proposals.map((item) => <article className="assistant-card" key={item.id}>
          <div className="assistant-card-top"><span className={`status status-${item.status === "pending_review" ? "submitted" : item.status === "project_created" ? "approved" : "rejected"}`}>{item.status === "pending_review" ? "Ожидает проверки" : item.status === "project_created" ? "Черновик создан" : "Отклонено"}</span><span className="muted small">{item.proposal.project_type}</span></div>
          <h3>{item.proposal.title}</h3><p>{item.proposal.description}</p>
          <div className="assistant-facts">
            <span>Бюджет <b>{money(item.proposal.budget_credits)} кр.</b></span>
            <span>Срок <b>{item.proposal.duration_periods} пер.</b></span>
            <span>Труд <b>{money(item.proposal.labor_fte)} FTE</b></span>
            <span>Энергия <b>{money(item.proposal.energy_kwh_per_period)} кВт·ч/пер.</b></span>
            <span className="assistant-fact-wide">Направления <b>{item.proposal.domain_codes.map((code) => domainNames[code] || code).join(", ")}</b></span>
          </div>
          {Object.keys(item.proposal.resource_requirements).length > 0 && <p className="assistant-note"><b>Ресурсные требования:</b> {Object.entries(item.proposal.resource_requirements).map(([key, value]) => `${key}: ${money(value)}`).join(" · ")}</p>}
          {item.proposal.rationale && <p className="assistant-note"><b>Обоснование:</b> {item.proposal.rationale}</p>}
          {item.proposal.assumptions.length > 0 && <BriefingList title="Допущения" items={item.proposal.assumptions} />}
          {item.proposal.risks.length > 0 && <BriefingList title="Риски" items={item.proposal.risks} />}
          {item.proposal.questions.length > 0 && <BriefingList title="Нужно уточнить" items={item.proposal.questions} />}
          {item.status === "pending_review" && (item.author_player_id === playerId || isCommander) && <div className="assistant-clarify">
            <label>Уточнить предложение
              <textarea value={clarifications[item.id] || ""} onChange={(event) => setClarifications((current) => ({ ...current, [item.id]: event.target.value }))}
                placeholder="Ответьте на вопросы ИИ или скорректируйте параметры проекта…" rows={2} maxLength={4000} />
            </label>
            <button className="button button-secondary small-button" disabled={Boolean(busy) || (clarifications[item.id] || "").trim().length < 2} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : "Добавьте уточнение не короче двух символов."}
              onClick={() => void clarifyProposal(item)}>{busy === `${item.id}-clarify` ? "Уточнение…" : `Уточнить предложение · v${item.version}`}</button>
          </div>}
          {item.status === "pending_review" && (item.author_player_id === playerId || isCommander) && <div className="assistant-actions">
            <button className="button button-primary small-button" disabled={Boolean(busy)} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : undefined} onClick={() => void proposalAction(item, "create-project")}>{busy === `${item.id}-create-project` ? "Создание…" : "Создать черновик проекта"}</button>
            <button className="button button-quiet small-button" disabled={Boolean(busy)} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : undefined} onClick={() => void proposalAction(item, "reject")}>Отклонить</button>
          </div>}
        </article>)}</div>}
    </section>

    <section className="panel assistant-panel">
      <div className="panel-heading"><div><div className="eyebrow">СЦЕНАРНЫЙ РЕЖИССЁР</div><h2>Сценарные события</h2></div><span className="count-chip">{events.length}</span></div>
      <p className="muted assistant-help">Сценарные карточки, подготовленные ИИ-ведущим и опубликованные командиром.</p>
      {isCommander && <div className="assistant-toolbar event-generation">
        <input value={eventFocus} onChange={(event) => setEventFocus(event.target.value)} maxLength={1000} placeholder="Фокус события, например: внешняя логистика или пылевая буря" />
        <button className="button button-primary" disabled={Boolean(busy)} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : "Для подготовки события требуется подключённый ИИ-провайдер. Без него кнопка сообщит, что именно нужно настроить."} onClick={() => void generateEvent()}>{busy === "event" ? "Подготовка…" : "Предложить событие"}</button>
      </div>}
      {events.length === 0 ? <p className="muted assistant-empty">Событий пока нет.</p> :
        <div className="assistant-cards">{events.map((item) => <article className="assistant-card" key={item.id}>
          <div className="assistant-card-top">
            <span className={`status status-${item.status === "published" ? "approved" : item.status === "pending_review" ? "submitted" : "rejected"}`}>{item.status === "published" ? "Опубликовано" : item.status === "pending_review" ? "На проверке" : "Отклонено"}</span>
            <span className="muted small">Период {item.period_created} · {item.proposal.severity} · оценочная вероятность {Math.round(item.proposal.likelihood * 100)}%</span>
          </div>
          <h3>{item.proposal.title}</h3><p>{item.proposal.narrative}</p>
          <p className="assistant-note"><b>Условие:</b> {item.proposal.trigger}</p>
          <BriefingList title="Варианты действий" items={item.proposal.player_options} />
          <BriefingList title="Возможные последствия" items={item.proposal.consequences} />
          {isCommander && item.status === "pending_review" && <div className="assistant-actions">
            <button className="button button-primary small-button" disabled={Boolean(busy)} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : undefined} onClick={() => void eventAction(item, "publish")}>Опубликовать в сессии</button>
            <button className="button button-quiet small-button" disabled={Boolean(busy)} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : undefined} onClick={() => void eventAction(item, "reject")}>Отклонить</button>
          </div>}
        </article>)}</div>}
    </section>


    <section className="panel assistant-panel">
      <div className="panel-heading"><div><div className="eyebrow">РЕЕСТР ЗНАНИЙ</div><h2>Реестр сущностей и версий</h2></div><span className="count-chip">{entities.length}</span></div>
      <p className="muted assistant-help">Технологии, инфраструктура, процессы, ресурсы и процедуры хранятся как версии базы знаний. Утверждение добавляет запись в реестр, но не меняет расчётную модель автоматически.</p>
      {canProposeEntity && <>
        <textarea className="assistant-input" value={entityText} onChange={(event) => setEntityText(event.target.value)}
          placeholder="Опишите новую технологию, оборудование, производственный процесс или процедуру…" rows={3} maxLength={8000} />
        <div className="assistant-toolbar"><span className="muted small">{entityText.length}/8000 · Нужна инженерная проверка</span>
          <button className="button button-primary" disabled={Boolean(busy) || entityText.trim().length < 5} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : "Введите описание не короче пяти символов."}
            onClick={() => void generateEntity()}>{busy === "entity" ? "Формирование…" : "Предложить сущность"}</button>
        </div>
      </>}
      {entities.length === 0 ? <p className="muted assistant-empty">Реестр пока пуст.</p> :
        <div className="assistant-cards">{entities.map((entity) => <article className="assistant-card" key={entity.id}>
          <div className="assistant-card-top">
            <span className={`status status-${entity.status === "active" ? "approved" : entity.status === "pending_review" ? "submitted" : "rejected"}`}>{entity.status === "active" ? "Утверждена" : entity.status === "pending_review" ? "На проверке" : entity.status === "superseded" ? "Предыдущая версия" : "Отклонена"}</span>
            <span className="muted small">v{entity.version} · {entity.entity_type}</span>
          </div>
          <h3>{entity.name}</h3><p>{entity.description}</p>
          <p className="assistant-note"><b>Идентификатор:</b> {entity.entity_key}</p>
          <p className="assistant-note"><b>Направления:</b> {entity.domain_codes.map((code) => domainNames[code] || code).join(", ")}</p>
          {entity.assumptions.length > 0 && <BriefingList title="Допущения" items={entity.assumptions} />}
          {entity.risks.length > 0 && <BriefingList title="Риски" items={entity.risks} />}
          {isCommander && entity.status === "pending_review" && <div className="assistant-actions">
            <button className="button button-primary small-button" disabled={Boolean(busy)} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : undefined} onClick={() => void entityAction(entity, "approve")}>Утвердить версию</button>
            <button className="button button-quiet small-button" disabled={Boolean(busy)} title={busy ? "Дождитесь завершения текущего запроса к ИИ." : undefined} onClick={() => void entityAction(entity, "reject")}>Отклонить</button>
          </div>}
        </article>)}</div>}
    </section>

    {error && <div className="alert assistant-alert">{error}</div>}
    {notice && <p className="assistant-notice">{notice}</p>}
  </div>;
}

function BriefingList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return <div className="briefing-list"><strong>{title}</strong><ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul></div>;
}
