import { useCallback, useEffect, useMemo, useState } from "react";
import Assistant from "./Assistant";

type Auth = { sessionId: string; token: string; playerId: string; displayName: string; joinCode?: string };
type StarterAsset = { name: string; kind: string; description: string; quantity?: number };
type StarterPreset = { id: string; name: string; tagline: string; description: string; strategy: string; tradeoffs: string; domain_packs: Record<string, StarterAsset[]> };
type CatalogEntity = {
  id: string; entity_type: string; name: string; description: string; domain_codes: string[];
  kind?: string; build?: Record<string, unknown>; research?: Record<string, unknown>;
  operation?: Record<string, unknown>; effects?: Record<string, unknown>;
};
type SessionData = {
  id: string;
  scenario_id: string;
  starter_preset?: { id: string; name: string; tagline: string; description: string; strategy: string; tradeoffs: string };
  current_period: number;
  settings?: { period_count: number; starting_resources: Record<string, number>; starting_credits: number };
  available_budget_credits: number;
  reserved_budget_credits: number;
  state: Record<string, unknown>;
  events?: Array<{ sequence: number; type: string; payload: Record<string, unknown>; created_at?: string | null }>;
  resource_catalog?: Record<string, { state_key?: string; unit?: string; precision_digits?: number }>;
};
type Player = { id: string; display_name: string; roles: string[]; responsibility_domains: string[] };
type Project = {
  id: string; author_player_id: string; version: number; title: string; description: string; project_type: string; status: string;
  domain_codes: string[]; budget_credits: number; progress_periods: number;
  duration_periods: number; labor_fte: number; energy_kwh_per_period: number;
  resource_requirements: Record<string, number>; expected_result: Record<string, unknown>;
  approvals?: Array<{ player_id: string; decision: string; comment: string; approval_mode: string }>;
};
type EventMessage = { sequence: number; event_type: string; payload: Record<string, unknown>; created_at?: string | null };
type View = "overview" | "workspace" | "map" | "projects" | "people" | "assistant" | "ai-settings" | "campaign-settings" | "tutorial";

const API = "/api/v1";
const money = (value: number) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(value);
const quantity = (value: number, digits = 1) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(value);
const numberAt = (source: Record<string, unknown>, key: string) => {
  const value = source[key];
  return typeof value === "number" ? value : 0;
};

async function request<T>(path: string, token?: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("X-Player-Token", token);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail || `Ошибка запроса: HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export default function App() {
  const [auth, setAuth] = useState<Auth | null>(() => {
    const saved = sessionStorage.getItem("marscolony.auth");
    return saved ? JSON.parse(saved) as Auth : null;
  });
  const [previousAuth, setPreviousAuth] = useState<Auth | null>(() => {
    const saved = sessionStorage.getItem("marscolony.previousAuth");
    return saved ? JSON.parse(saved) as Auth : null;
  });
  const [session, setSession] = useState<SessionData | null>(null);
  const [players, setPlayers] = useState<Player[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [events, setEvents] = useState<EventMessage[]>([]);
  const [view, setView] = useState<View>("overview");
  const [tutorialStep, setTutorialStep] = useState(0);
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const [connectionDetail, setConnectionDetail] = useState("Ожидание WebSocket-соединения.");
  const screenMode = new URLSearchParams(window.location.search).get("view") === "screen";

  const refresh = useCallback(async () => {
    if (!auth) return;
    const [nextSession, nextPlayers, nextProjects] = await Promise.all([
      request<SessionData>(`${API}/sessions/${auth.sessionId}`, auth.token),
      request<{ players: Player[] }>(`${API}/sessions/${auth.sessionId}/players`, auth.token),
      request<{ projects: Project[] }>(`${API}/sessions/${auth.sessionId}/projects`, auth.token)
    ]);
    setSession(nextSession);
    setEvents((nextSession.events || []).slice(-12).map((event) => ({ sequence: event.sequence, event_type: event.type, payload: event.payload, created_at: event.created_at })));
    setPlayers(nextPlayers.players);
    setProjects(nextProjects.projects);
  }, [auth]);

  useEffect(() => {
    if (!auth) return;
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    const connect = () => {
      if (!active) return;
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}${API}/sessions/${auth.sessionId}/stream`);
      socket.onopen = () => { setReconnecting(false); setConnectionDetail("WebSocket открыт; выполняется аутентификация игрока."); socket?.send(JSON.stringify({ type: "authenticate", player_token: auth.token })); };
      socket.onmessage = (message) => {
        const data = JSON.parse(message.data) as { type: string; last_sequence?: number; sequence?: number; event_type?: string; payload?: Record<string, unknown>; created_at?: string | null };
        if (data.type === "snapshot") {
          setConnected(true); setReconnecting(false); setError(""); setConnectionDetail("Сервер подтвердил игрока и отправил актуальный снимок сессии.");
          void refresh().catch((reason: Error) => setError(reason.message));
        } else if (data.type === "heartbeat") {
          setConnected(true); setReconnecting(false); setConnectionDetail("Серверный heartbeat получен: " + new Date().toLocaleTimeString("ru-RU"));
        } else if (data.type === "event" && typeof data.sequence === "number") {
          setEvents((previous) => [...previous, {
            sequence: data.sequence!, event_type: data.event_type || "session.updated", payload: data.payload || {}, created_at: data.created_at
          }].slice(-12));
          void refresh().catch((reason: Error) => setError(reason.message));
        }
      };
      socket.onclose = (event) => {
        setConnected(false);
        setConnectionDetail(`WebSocket закрыт: код ${event.code}${event.reason ? " · " + event.reason : ""}.`);
        if (event.code === 4401 || event.code === 4404) {
          setReconnecting(false);
          setError(event.code === 4401
            ? "Сессионный токен недействителен. Войдите в игровую сессию заново."
            : "Игровая сессия не найдена.");
          return;
        }
        setReconnecting(true);
        if (active) reconnectTimer = window.setTimeout(() => setRetry((value) => value + 1), 1500);
      };
      socket.onerror = () => { setConnected(false); setReconnecting(true); setConnectionDetail("Ошибка WebSocket. Подробности доступны в консоли браузера и логах API/Nginx."); };
    };
    void refresh().catch((reason: Error) => setError(reason.message));
    connect();
    return () => {
      active = false;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [auth, retry, refresh]);

  const advancePeriod = async () => {
    if (!auth || !session) return;
    setBusy(true); setError("");
    try {
      await request(`${API}/sessions/${auth.sessionId}/advance`, auth.token, {
        method: "POST", body: JSON.stringify({ expected_period: session.current_period })
      });
      await refresh();
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };

  const leave = () => {
    sessionStorage.removeItem("marscolony.auth");
    sessionStorage.removeItem("marscolony.previousAuth");
    setPreviousAuth(null);
    setAuth(null);
    setSession(null);
    setConnected(false); setReconnecting(false);
    setEvents([]);
  };

  const returnToPreviousSession = () => {
    if (!previousAuth) return;
    sessionStorage.setItem("marscolony.auth", JSON.stringify(previousAuth));
    sessionStorage.removeItem("marscolony.previousAuth");
    setAuth(previousAuth);
    setPreviousAuth(null);
    setSession(null);
    setPlayers([]);
    setProjects([]);
    setEvents([]);
    setConnected(false);
    setReconnecting(false);
    setTutorialStep(0);
    setView("overview");
  };

  const createSession = async (presetId: string) => {
    setBusy(true); setError("");
    try {
      const result = await request<SessionData & { owner_token: string; owner_player_id: string; join_code: string }>(
        `${API}/sessions`, undefined, { method: "POST", body: JSON.stringify({ scenario_id: "ares-01", starter_preset_id: presetId }) }
      );
      const next: Auth = {
        sessionId: result.id, token: result.owner_token, playerId: result.owner_player_id,
        displayName: "Командир колонии", joinCode: result.join_code
      };
      if (auth && !previousAuth) {
        sessionStorage.setItem("marscolony.previousAuth", JSON.stringify(auth));
        setPreviousAuth(auth);
      }
      sessionStorage.setItem("marscolony.auth", JSON.stringify(next));
      setTutorialStep(0);
      setAuth(next);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };

  const joinSession = async (sessionId: string, displayName: string, inviteCode: string) => {
    setBusy(true); setError("");
    try {
      const result = await request<{ player_id: string; player_token: string; display_name: string }>(
        `${API}/sessions/${sessionId}/players`, undefined,
        { method: "POST", body: JSON.stringify({ display_name: displayName, invite_code: inviteCode }) }
      );
      const next: Auth = { sessionId, token: result.player_token, playerId: result.player_id, displayName: result.display_name };
      sessionStorage.setItem("marscolony.auth", JSON.stringify(next));
      setAuth(next);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };

  const commander = useMemo(() => players.find((player) => player.id === auth?.playerId), [players, auth]);
  const state = session?.state || {};
  const infrastructure = Array.isArray(state.infrastructure) ? state.infrastructure as Array<Record<string, unknown>> : [];
  const technologies = Array.isArray(state.technologies) ? state.technologies as Array<Record<string, unknown>> : [];

  if (!auth) return <Login busy={busy} error={error} onCreate={createSession} onJoin={joinSession} />;

  return (
    <div className={screenMode ? "app screen-mode" : "app"}>
      <header className="topbar">
        <a className="brand" href="/" aria-label="MarsColony">
          <span className="brand-mark">M</span><span>MARS<span className="brand-light">COLONY</span></span>
        </a>
        <div className="topbar-right">
          <span className={connected ? "connection online" : "connection"} title={connectionDetail}><i />{connected ? "Синхронизация активна" : reconnecting ? "Связь потеряна · переподключение" : "Подключение…"}</span>
          {!screenMode && <button className="button button-quiet" onClick={leave}>Выйти</button>}
        </div>
      </header>

      <main className="workspace">
        <aside className="sidebar">
          <div className="session-label">ТЕКУЩАЯ СЕССИЯ</div>
          <div className="session-id">{auth.sessionId.slice(0, 8).toUpperCase()}</div>
          <div className="nav-list">
            {([
              ["overview", "Обзор колонии", "◫"], ["workspace", "Мой кабинет", "▣"], ["map", "Карта колонии", "⌖"],
              ["projects", "Проекты", "▤"], ["people", "Участники", "♙"],
              ["assistant", "ИИ-ведущий", "✧"], ["ai-settings", "Настройки ИИ", "⚙"], ["campaign-settings", "Параметры игры", "◷"], ["tutorial", "Обучение", "?"]
            ] as Array<[View, string, string]>).filter(([key]) => !["ai-settings", "campaign-settings"].includes(key) || Boolean(commander?.roles.includes("colony_commander")) ).map(([key, label, icon]) =>
              <button key={key} className={view === key ? "nav-item active" : "nav-item"} onClick={() => setView(key)}>
                <span className="nav-icon">{icon}</span>{label}
                {key === "projects" && projects.length > 0 && <span className="nav-count">{projects.length}</span>}
              </button>
            )}
          </div>
          <div className="sidebar-bottom">
            <div className="user-avatar">{auth.displayName.slice(0, 1).toUpperCase()}</div>
            <div><strong>{auth.displayName}</strong><span>{commander?.roles?.join(", ") || "Участник"}</span></div>
          </div>
        </aside>

        <section className="content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">ARES-01 / {session?.starter_preset?.name || "MARS OPERATIONS"}</div>
              <h1>{view === "overview" ? "Центр управления" : view === "workspace" ? "Мой кабинет" : view === "map" ? "Карта колонии" : view === "projects" ? "Проектный офис" : view === "people" ? "Состав экспедиции" : view === "tutorial" ? "Обучение экипажа" : view === "ai-settings" ? "Настройки ИИ" : view === "campaign-settings" ? "Параметры игры" : "ИИ-ведущий"}</h1>
              <p>{view === "overview" ? "Оперативная картина состояния базы и ход экспедиции." : view === "workspace" ? "Персональные обязанности, показатели и задачи вашей зоны ответственности." : view === "map" ? "Интерактивная схема инфраструктуры и расположения объектов." : view === "projects" ? "Инициативы, согласования и ход исполнения." : view === "people" ? "Участники сессии и распределение ответственности." : view === "tutorial" ? "Учебная миссия: от первого осмотра до завершения проекта." : view === "ai-settings" ? "Подключение провайдера ИИ для всей игровой сессии." : view === "campaign-settings" ? "Продолжительность кампании и финансовые параметры. Стартовая инфраструктура выбирается при создании сессии." : "Сводки колонии, проектные предложения и сценарные события."}</p>
            </div>
            <div className="period-chip"><span>ТЕКУЩИЙ ПЕРИОД</span><strong>{session?.current_period ?? "—"} <small>/ {session?.settings?.period_count ?? 4}</small></strong></div>
          </div>

          {error && <div className="alert">{error}<button onClick={() => setError("")}>×</button></div>}

          {view === "overview" && <Overview session={session} state={state} projects={projects} events={events} isCommander={Boolean(commander?.roles.includes("colony_commander"))} busy={busy} onAdvance={advancePeriod} onOpenProjects={() => setView("projects")} />}
          {view === "workspace" && <RoleWorkspace player={commander} players={players} projects={projects} events={events} state={state} currentPeriod={session?.current_period ?? 0} availableBudget={session?.available_budget_credits ?? 0} reservedBudget={session?.reserved_budget_credits ?? 0} onNavigate={setView} />}
          {view === "map" && <ColonyMap assets={infrastructure} />}
          {view === "projects" && <ProjectList projects={projects} auth={auth} player={commander} infrastructure={infrastructure} resourceCatalog={session?.resource_catalog || {}} onRefresh={refresh} />}
          {view === "ai-settings" && commander?.roles.includes("colony_commander") && <AISettings /> }
          {view === "campaign-settings" && commander?.roles.includes("colony_commander") && session && <CampaignSettings session={session} auth={auth} hasProjects={projects.length > 0} onRefresh={refresh} />}
          {view === "people" && <People players={players} technologies={technologies} auth={auth} onRefresh={refresh} />}
          {view === "assistant" && <Assistant sessionId={auth.sessionId} token={auth.token} playerId={auth.playerId} eventWatermark={events[events.length - 1]?.sequence ?? 0} isCommander={Boolean(commander?.roles.includes("colony_commander"))} canProposeEntity={Boolean(commander?.roles.some((role) => ["colony_commander", "domain_lead", "engineer"].includes(role)))} onRefresh={refresh} />}
          {view === "tutorial" && <Tutorial step={tutorialStep} onStepChange={setTutorialStep} onNavigate={setView} onCreateTrainingSession={() => createSession("balanced")} onReturnToPreviousSession={returnToPreviousSession} hasPreviousSession={Boolean(previousAuth)} busy={busy} />}

          {auth.joinCode && <div className="invite-strip">
            <div className="invite-values">
              <div><span>ПОЛНЫЙ ID СЕССИИ</span><strong className="invite-code">{auth.sessionId}</strong></div>
              <div><span>КОД ПРИГЛАШЕНИЯ</span><strong className="invite-code">{auth.joinCode}</strong></div>
            </div>
            <div className="invite-actions">
              <button className="button button-quiet" onClick={() => copyInviteValue(auth.sessionId)}>Копировать ID</button>
              <button className="button button-quiet" onClick={() => copyInviteValue(auth.joinCode || "")}>Копировать код</button>
              <button className="button button-secondary" onClick={() => copyInviteValue(`ID: ${auth.sessionId}\nКод: ${auth.joinCode}`)}>Копировать приглашение</button>
            </div>
          </div>}
        </section>
      </main>
    </div>
  );
}


const ROLE_INFO: Record<string, { title: string; purpose: string; duties: string[] }> = {
  colony_commander: { title: "Командир колонии", purpose: "Обеспечивает целостность управления колонией и принимает итоговые решения.", duties: ["Приоритеты и общий план развития", "Назначение ролей и зон ответственности", "Командирские решения и продвижение периода", "Контроль межведомственных рисков и критических резервов"] },
  domain_lead: { title: "Руководитель направления", purpose: "Отвечает за результаты закреплённых функциональных направлений.", duties: ["Готовит планы и проекты в своей зоне", "Следит за профильными показателями и отклонениями", "Согласует проекты, затрагивающие его направление", "Докладывает о рисках и потребности в ресурсах"] },
  finance_lead: { title: "Финансовый руководитель", purpose: "Контролирует бюджет, финансовую доступность инициатив и обязательства колонии.", duties: ["Контроль доступного и зарезервированного бюджета", "Финансовая оценка проектных заявок", "Контроль затрат и финансовых рисков", "Подготовка вариантов распределения средств"] },
  engineer: { title: "Инженер", purpose: "Разрабатывает технические решения и сопровождает их реализацию в назначенных направлениях.", duties: ["Подготовка технических параметров проектов", "Проверка реализуемости и ограничений", "Контроль исполнения и состояния объектов", "Подготовка предложений по ремонту и модернизации"] },
  player: { title: "Участник экипажа", purpose: "Участвует в обсуждении и выполнении задач; полномочия зависят от назначенной роли.", duties: ["Изучает состояние колонии", "Предлагает решения ответственным игрокам", "Выполняет порученные задачи", "Следит за уведомлениями и изменениями"] }
};
const DOMAIN_INFO: Record<string, { title: string; purpose: string; metrics: Array<[string, string, string]> }> = {
  geology: { title: "Геологоразведка и добыча", purpose: "Оценка запасов, разведка и добыча местного сырья.", metrics: [["raw_ice_kg","Запас сырого льда","кг"]] },
  industry: { title: "Промышленность и производство", purpose: "Переработка сырья, выпуск оборудования, ремонт и производственные мощности.", metrics: [["technologies","Зарегистрировано технологий","шт."]] },
  energy: { title: "Энергетика", purpose: "Генерация, накопители, распределение нагрузки и резервирование.", metrics: [["power_generation_kw","Мощность генерации","кВт"],["battery_soc_kwh","Заряд аккумуляторов","кВт·ч"]] },
  life_support: { title: "Жизнеобеспечение", purpose: "Вода, кислород, атмосфера, тепло и непрерывность критических контуров.", metrics: [["oxygen_kg","Запас кислорода","кг"],["prepared_water_kg","Подготовленная вода","кг"]] },
  construction: { title: "Строительство и инфраструктура", purpose: "Размещение, строительство и обслуживание объектов колонии.", metrics: [["infrastructure","Объектов инфраструктуры","шт."]] },
  agriculture: { title: "Сельское хозяйство и биотехнологии", purpose: "Производство пищи, гидропоника и биологические циклы.", metrics: [["dry_food_kg","Пищевые запасы","кг"]] },
  medicine: { title: "Медицина и санитария", purpose: "Здоровье экипажа, медицинская инфраструктура и санитарная безопасность.", metrics: [] },
  social: { title: "Социальное развитие и демография", purpose: "Жильё, качество жизни и социальная устойчивость.", metrics: [] },
  education: { title: "Образование и кадры", purpose: "Подготовка специалистов и кадровое планирование.", metrics: [["technologies","Технологий в реестре","шт."]] },
  science: { title: "Наука и технологии", purpose: "Исследования, испытания, робототехника и технологическая зрелость.", metrics: [["technologies","Технологий в реестре","шт."]] },
  planetary: { title: "Планетология и исследование территории", purpose: "Картография, оценка ресурсов и территориальных опасностей.", metrics: [["raw_ice_kg","Запас сырого льда","кг"]] },
  transport: { title: "Транспорт и логистика", purpose: "Маршруты, перевозки, складские потоки и грузовые окна.", metrics: [] },
  administration: { title: "Администрация и экономика", purpose: "Бюджет, приоритеты, финансирование и обязательства.", metrics: [["budget_credits","Доступный бюджет","кр."]] },
  safety: { title: "Безопасность и аварийное реагирование", purpose: "Предупреждение аварий, инспекции и реагирование.", metrics: [["oxygen_kg","Запас кислорода","кг"],["battery_soc_kwh","Заряд аккумуляторов","кВт·ч"]] },
  communications: { title: "Связь и информационные системы", purpose: "Связь, вычислительные ресурсы, данные и автоматизация.", metrics: [] },
  external_relations: { title: "Межпланетные связи и снабжение", purpose: "Поставки, контракты, импорт и взаимодействие с Землёй.", metrics: [["budget_credits","Доступный бюджет","кр."]] }
};
function RoleWorkspace({ player, players, projects, events, state, currentPeriod, availableBudget, reservedBudget, onNavigate }: {
  player: Player | undefined; players: Player[]; projects: Project[]; events: EventMessage[]; state: Record<string, unknown>;
  currentPeriod: number; availableBudget: number; reservedBudget: number; onNavigate: (view: View) => void;
}) {
  if (!player) return <section className="panel"><h2>Профиль участника не загружен</h2><p className="muted">Обновите страницу или переподключитесь к сессии.</p></section>;
  const roles = player.roles.length ? player.roles : ["player"];
  const domains = player.responsibility_domains || [];
  const scopedProjects = roles.includes("finance_lead") ? projects : projects.filter((project) => domains.some((domain) => project.domain_codes.includes(domain)));
  const pending = scopedProjects.filter((project) => project.status === "submitted");
  const active = scopedProjects.filter((project) => project.status === "executing");
  const infrastructure = Array.isArray(state.infrastructure) ? state.infrastructure as Array<Record<string, unknown>> : [];
  const relevantEvents = events.filter((event) => {
    const projectId = String(event.payload.project_id || "");
    if (projectId) return scopedProjects.some((project) => project.id === projectId);
    const actorId = String(event.payload.player_id || event.payload.assigned_by || "");
    return actorId === player.id;
  }).slice(-6).reverse();
  const metricValue = (key: string) => {
    if (key === "infrastructure") return infrastructure.length;
    if (key === "technologies") return Array.isArray(state.technologies) ? state.technologies.length : 0;
    const value = state[key];
    return typeof value === "number" ? quantity(value, 2) : "Нет данных";
  };
  return <div className="role-workspace">
    <section className="panel role-profile">
      <div className="eyebrow">PERSONAL RESPONSIBILITY / PERIOD {currentPeriod}</div>
      <h2>{player.display_name}</h2>
      <div className="role-tags">{roles.map((role) => <span className="role-tag" key={role}>{ROLE_INFO[role]?.title || role}</span>)}</div>
      {roles.map((role) => <div className="role-description" key={role}><h3>{ROLE_INFO[role]?.title || role}</h3><p>{ROLE_INFO[role]?.purpose || "Роль участника игровой сессии."}</p><ul>{(ROLE_INFO[role]?.duties || ROLE_INFO.player.duties).map((duty) => <li key={duty}>{duty}</li>)}</ul></div>)}
    </section>
    {roles.includes("finance_lead") && <section className="panel finance-workspace-metrics"><div className="eyebrow">FINANCIAL CONTROL</div><h3>Финансовая картина</h3><div className="domain-metrics"><div><span>Доступный бюджет</span><strong>{quantity(availableBudget, 1)} <small>кр.</small></strong></div><div><span>Зарезервировано</span><strong>{quantity(reservedBudget, 1)} <small>кр.</small></strong></div><div><span>Проекты на финансовом рассмотрении</span><strong>{pending.length} <small>шт.</small></strong></div></div></section>}
    <section className="role-summary-grid">
      <article className="metric-card"><span>АКТИВНЫЕ ПРОЕКТЫ</span><div><strong>{active.length}</strong><small>в работе</small></div><p>В закреплённых направлениях</p></article>
      <article className="metric-card"><span>ТРЕБУЮТ РЕШЕНИЯ</span><div><strong>{pending.length}</strong><small>заявок</small></div><p>Ожидают согласования</p></article>
      <article className="metric-card"><span>УЧАСТНИКОВ</span><div><strong>{players.length}</strong><small>человек</small></div><p>В общей сессии</p></article>
    </section>
    <section className="panel">
      <div className="panel-heading"><div><div className="eyebrow">YOUR DOMAINS</div><h2>Зоны ответственности</h2></div><span className="count-chip">{domains.length}</span></div>
      {domains.length === 0 ? <p className="muted">За вами пока не закреплены направления. Попросите Капитана назначить зону ответственности.</p> : domains.map((code) => {
        const info = DOMAIN_INFO[code] || { title: code, purpose: "Описание направления не задано.", metrics: [] as Array<[string,string,string]> };
        const related = projects.filter((project) => project.domain_codes.includes(code));
        const relatedAssets = infrastructure.filter((asset) => Array.isArray(asset.domain_codes) && asset.domain_codes.includes(code));
        return <article className="domain-work-card" key={code}>
          <h3>{info.title}</h3><p>{info.purpose}</p>
          <div className="domain-metrics">{info.metrics.map(([key,label,unit]) => <div key={key}><span>{label}</span><strong>{metricValue(key)} <small>{unit}</small></strong></div>)}<div><span>Инфраструктурные объекты направления</span><strong>{infrastructure.filter((asset) => Array.isArray(asset.domain_codes) && asset.domain_codes.includes(code)).length} <small>шт.</small></strong></div><div><span>Активные проекты направления</span><strong>{related.filter((project) => ["approved","executing"].includes(project.status)).length} <small>шт.</small></strong></div></div>
          <div className="domain-projects"><strong>Стартовый комплект · {relatedAssets.length} объектов</strong>{relatedAssets.map((asset) => <span key={String(asset.id)}>{String(asset.name || asset.kind || "Инфраструктурный объект")}</span>)}</div>
          <div className="domain-projects"><strong>{related.length} проектов</strong>{related.slice(0,3).map((project) => <span key={project.id}>{project.title} · {project.status}</span>)}</div>
        </article>;
      })}
    </section>
    <section className="panel">
      <div className="panel-heading"><div><div className="eyebrow">ACTION QUEUE</div><h2>Мои задачи и решения</h2></div><button className="button button-secondary small-button" onClick={() => onNavigate("projects")}>Открыть проекты</button></div>
      {pending.length === 0 && active.length === 0 ? <p className="muted">Нет активных задач в закреплённых направлениях.</p> : [...pending,...active].map((project) => <div className="role-task-row" key={project.id}><strong>{project.title}</strong><span>{project.status === "submitted" ? "Ожидает согласования" : "Исполняется"} · {project.domain_codes.map((domain) => DOMAIN_INFO[domain]?.title || domain).join(", ")}</span></div>)}
    </section>
    <section className="panel"><div className="eyebrow">RECENT ACTIVITY</div><h2>Последние события моей зоны</h2>{relevantEvents.length === 0 ? <p className="muted">Связанных событий в доступной истории пока нет.</p> : relevantEvents.map((event) => <div className="role-task-row" key={event.sequence}><strong>{event.event_type}</strong><span>#{event.sequence} · {event.created_at ? new Date(event.created_at).toLocaleString("ru-RU") : "время не указано"}</span></div>)}</section>
  </div>;
}

function CampaignSettings({ session, auth, hasProjects, onRefresh }: {
  session: SessionData;
  auth: Auth;
  hasProjects: boolean;
  onRefresh: () => Promise<void>;
}) {
  const catalog = session.resource_catalog || {};
  const [periodCount, setPeriodCount] = useState(String(session.settings?.period_count ?? 4));
  const [startingCredits, setStartingCredits] = useState(String(session.settings?.starting_credits ?? 0));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const locked = session.current_period > 0 || hasProjects;
  const valid = Number.isInteger(Number(periodCount)) && Number(periodCount) >= 1 && Number(periodCount) <= 100
    && Number.isFinite(Number(startingCredits)) && Number(startingCredits) >= 0;
  const domainLabels: Record<string, string> = {
    geology: "Геологоразведка и добыча", industry: "Промышленность и производство",
    energy: "Энергетика", life_support: "Жизнеобеспечение", construction: "Строительство и инфраструктура",
    agriculture: "Сельское хозяйство", medicine: "Медицина", social: "Социальное развитие",
    education: "Образование", science: "Наука и технологии", planetary: "Планетология",
    transport: "Транспорт и логистика", administration: "Администрация и экономика",
    safety: "Безопасность", communications: "Связь", external_relations: "Внешнее снабжение"
  };
  const infrastructure = Array.isArray(session.state.infrastructure)
    ? session.state.infrastructure as Array<Record<string, unknown>> : [];
  const starterAssets = infrastructure.filter((asset) =>
    (asset.properties as Record<string, unknown> | undefined)?.starter_asset === true
  );
  const materialStockLabels: Record<string, string> = {
    oxygen: "Кислород", food_solids: "Пищевые запасы",
    usable_water: "Подготовленная вода", raw_ice: "Сырой лёд"
  };
  useEffect(() => {
    setPeriodCount(String(session.settings?.period_count ?? 4));
    setStartingCredits(String(session.settings?.starting_credits ?? 0));
  }, [session.settings]);
  const save = async () => {
    if (locked || !valid) return;
    setSaving(true); setError(""); setNotice("");
    try {
      await request(`${API}/sessions/${auth.sessionId}/settings`, auth.token, {
        method: "PATCH",
        body: JSON.stringify({ period_count: Number(periodCount), starting_credits: Number(startingCredits) })
      });
      await onRefresh();
      setNotice("Параметры кампании сохранены. Они применяются ко всем участникам этой сессии.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setSaving(false); }
  };
  return <div className="ai-settings-layout">
    <section className="panel">
      <div className="eyebrow">CAMPAIGN CONFIGURATION</div><h2>Параметры игровой кампании</h2>
      <p className="muted ai-settings-help">Здесь задаются продолжительность и финансовый резерв. Стартовая техническая и инфраструктурная комплектация выбирается капитаном на экране создания сессии.</p>
      {locked && <div className="alert">{session.current_period > 0 ? "Кампания уже началась." : "Уже создан проект."} Изменение параметров заблокировано.</div>}
      <label>Количество игровых периодов<input type="number" min="1" max="100" step="1" value={periodCount} disabled={locked} onChange={(event) => setPeriodCount(event.target.value)} /><small>Один период сценария Ares-1 равен 30 солам. Общая длительность пересчитывается автоматически.</small></label>
      <label>Стартовый бюджет, игровые кредиты<input type="number" min="0" step="0.1" value={startingCredits} disabled={locked} onChange={(event) => setStartingCredits(event.target.value)} /><small>Начальный финансовый резерв колонии.</small></label>
      <div className="form-section">
        <strong>Стартовые материальные запасы по сценарию</strong>
        <p className="field-help">Это расходуемые вещества и материалы, а не технические объекты. Они задаются сценарием отдельно от стартовой инфраструктуры.</p>
        <div className="system-list">{Object.entries(catalog).map(([key, resource]) => {
          const value = session.settings?.starting_resources?.[resource.state_key || ""] ?? 0;
          return <div className="system-row" key={key}><div className="system-info"><strong>{materialStockLabels[key] || key}</strong><span>{resource.unit || "ед."}</span></div><div className="system-value">{quantity(value, resource.precision_digits ?? 2)}</div></div>;
        })}</div>
      </div>
      {error && <div className="alert">{error}</div>}
      {notice && <p className="assistant-notice">{notice}</p>}
      <div className="form-foot"><span className="muted">Текущий период: {session.current_period} из {session.settings?.period_count ?? 4}.</span><button className="button button-primary" disabled={locked || saving || !valid} title={locked ? "Параметры заблокированы после начала кампании или создания первого проекта." : saving ? "Сохранение выполняется." : !valid ? "Проверьте количество периодов и стартовый бюджет." : "Сохранить параметры кампании."} onClick={() => void save()}>{saving ? "Сохранение…" : "Сохранить параметры"}</button></div>
    </section>
    <section className="panel">
      <div className="eyebrow">STARTING INFRASTRUCTURE</div><h2>Выбранная комплектация</h2>
      <h3>{session.starter_preset?.name || "Стартовый профиль не указан"}</h3>
      <p className="muted">{session.starter_preset?.description || "Профиль стартовой инфраструктуры не сохранён в снимке сессии."}</p>
      <div className="system-row"><div className="system-info"><strong>Инфраструктурных объектов</strong><span>В начальном составе колонии</span></div><div className="system-value">{starterAssets.length}</div></div>
      {Object.entries(domainLabels).map(([domain, label]) => {
        const assets = starterAssets.filter((asset) => Array.isArray(asset.domain_codes) && asset.domain_codes.includes(domain));
        return <details className="starter-preset-details" key={domain}><summary>{label} · {assets.length} объектов</summary>{assets.map((asset) => <div key={String(asset.id)}><strong>{String(asset.name || asset.kind)}</strong><span>{String((asset.properties as Record<string, unknown> | undefined)?.description || "")}</span></div>)}</details>;
      })}
      <p className="field-help">Выбранный профиль фиксируется при создании сессии. Для смены стартовой комплектации нужно создать новую сессию.</p>
    </section>
  </div>;
}


function AISettings() {
  const [provider, setProvider] = useState("yandex_ai_studio");
  const [apiKey, setApiKey] = useState("");
  const [folderId, setFolderId] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("yandexgpt/latest");
  const [notice, setNotice] = useState("");
  const config = provider === "yandex_ai_studio"
    ? "MARS_AI_PROVIDER=yandex_ai_studio\nMARS_AI_YANDEX_API_KEY=" + (apiKey || "<вставьте_ключ>") + "\nMARS_AI_YANDEX_FOLDER_ID=" + (folderId || "<идентификатор_каталога>") + "\nMARS_AI_YANDEX_MODEL=" + (model || "yandexgpt/latest")
    : "MARS_AI_PROVIDER=openai_compatible\nMARS_AI_BASE_URL=" + (baseUrl || "https://api.example.com/v1") + "\nMARS_AI_API_KEY=" + (apiKey || "<вставьте_ключ>") + "\nMARS_AI_MODEL=" + (model || "<название_модели>");
  const copyConfig = () => {
    if (!navigator.clipboard?.writeText) {
      window.prompt("Скопируйте конфигурацию для .env:", config);
      setNotice("Скопируйте конфигурацию в .env рядом с docker-compose.yml и перезапустите API.");
      return;
    }
    void navigator.clipboard.writeText(config).then(() => setNotice("Конфигурация скопирована. Вставьте её в .env рядом с docker-compose.yml и перезапустите API.")).catch(() => {
      window.prompt("Скопируйте конфигурацию для .env:", config);
      setNotice("Скопируйте конфигурацию в .env рядом с docker-compose.yml и перезапустите API.");
    });
  };
  return <div className="ai-settings-layout">
    <section className="panel">
      <div className="eyebrow">AI PROVIDER / SERVER CONFIGURATION</div><h2>Подключение ИИ-ведущего</h2>
      <p className="muted ai-settings-help">Настройка выполняется для всей игровой сессии. Ключи не сохраняются в браузере после закрытия страницы и не отправляются в игровой API: панель формирует блок для серверного файла .env. Не вставляйте секреты в общий чат и не публикуйте .env в Git.</p>
      <label>Провайдер<select value={provider} onChange={(event) => { setProvider(event.target.value); setNotice(""); }}>{[["yandex_ai_studio","Yandex AI Studio"],["openai_compatible","OpenAI-compatible API"]].map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      {provider === "openai_compatible" && <label>Базовый URL API<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1" /></label>}
      {provider === "yandex_ai_studio" && <label>ID каталога Yandex Cloud<input value={folderId} onChange={(event) => setFolderId(event.target.value)} placeholder="b1g..." /></label>}
      <label>Название модели<input value={model} onChange={(event) => setModel(event.target.value)} placeholder="yandexgpt/latest" /></label>
      <label>API-ключ<input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="Вставьте API-ключ" /></label>
      <div className="form-foot"><span className="muted">После изменения .env выполните <code>docker compose up -d --force-recreate api</code>.</span><button className="button button-primary" onClick={copyConfig}>Скопировать настройки</button></div>
      {notice && <p className="assistant-notice">{notice}</p>}
    </section>
    <section className="panel"><div className="eyebrow">CONFIGURATION PREVIEW</div><h2>Блок для .env</h2><pre className="env-preview">{config}</pre><p className="muted small">AI Gateway поддерживает несколько провайдеров и маршрутизацию задач через MARS_AI_PROVIDERS и MARS_AI_TASK_ROUTING. Эта форма пока генерирует конфигурацию одного провайдера.</p></section>
  </div>;
}

function copyInviteValue(value: string) {
  if (navigator.clipboard?.writeText) {
    void navigator.clipboard.writeText(value).catch(() => {
      window.prompt("Скопируйте данные приглашения:", value);
    });
  } else {
    window.prompt("Скопируйте данные приглашения:", value);
  }
}

function Login({ busy, error, onCreate, onJoin }: {
  busy: boolean; error: string;
  onCreate: (presetId: string) => void;
  onJoin: (sessionId: string, displayName: string, inviteCode: string) => Promise<void>;
}) {
  const [sessionId, setSessionId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [presets, setPresets] = useState<StarterPreset[]>([]);
  const [selectedPreset, setSelectedPreset] = useState("balanced");
  const [presetError, setPresetError] = useState("");
  useEffect(() => {
    let active = true;
    request<{ presets: StarterPreset[] }>(`${API}/scenarios/ares-01/starter-presets`)
      .then((result) => { if (active) setPresets(result.presets); })
      .catch((reason) => { if (active) setPresetError((reason as Error).message); });
    return () => { active = false; };
  }, []);
  const domainLabels: Record<string, string> = {
    geology: "Добыча", industry: "Промышленность", energy: "Энергетика", life_support: "Жизнеобеспечение",
    construction: "Строительство", agriculture: "Агрокомплекс", medicine: "Медицина", social: "Социальная сфера",
    education: "Образование", science: "Наука", planetary: "Планетология", transport: "Транспорт",
    administration: "Управление", safety: "Безопасность", communications: "Связь", external_relations: "Снабжение"
  };
  return <div className="login-page">
    <div className="login-aside">
      <div className="brand"><span className="brand-mark">M</span><span>MARS<span className="brand-light">COLONY</span></span></div>
      <div className="login-art"><div className="planet" /><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="base-glow" /></div>
      <div className="login-caption"><div className="eyebrow">HUMANITY / NEXT FRONTIER</div><h1>Новая глава<br />начинается здесь.</h1><p>Единый центр управления марсианской экспедицией. Решения, ресурсы и люди — в одной системе.</p></div>
      <span className="aside-foot">ARES PROGRAM · MARS COLONY SIMULATION</span>
    </div>
    <div className="login-panel">
      <div className="login-form-wrap">
        <div className="eyebrow">ДОБРО ПОЖАЛОВАТЬ</div><h2>Создание колонии</h2><p className="muted">Выберите стартовую комплектацию инфраструктуры. Каждый профиль включает объекты для всех 16 зон ответственности.</p>
        <div className="starter-preset-list">
          {presets.map((preset) => {
            const assets = Object.values(preset.domain_packs).flat();
            const objectCount = assets.reduce((sum, asset) => sum + (asset.quantity || 1), 0);
            const domainCount = Object.keys(preset.domain_packs).length;
            return <button type="button" key={preset.id} className={selectedPreset === preset.id ? "starter-preset-card selected" : "starter-preset-card"} disabled={busy} onClick={() => setSelectedPreset(preset.id)}>
              <span className="starter-preset-heading"><strong>{preset.name}</strong><span>{selectedPreset === preset.id ? "ВЫБРАНО" : "ВЫБРАТЬ"}</span></span>
              <span className="starter-preset-tagline">{preset.tagline}</span>
              <span className="starter-preset-description">{preset.description}</span>
              <span className="starter-preset-stats"><b>{objectCount}</b> объектов · <b>{domainCount}</b> направлений</span>
              <span className="starter-preset-domains">{Object.entries(preset.domain_packs).map(([domain, items]) => <span key={domain}>{domainLabels[domain] || domain}: {items.reduce((sum, item) => sum + (item.quantity || 1), 0)}</span>)}</span>
              <details className="starter-preset-details">
                <summary>Состав комплекта по направлениям</summary>
                {Object.entries(preset.domain_packs).map(([domain, items]) => <div key={domain}><strong>{domainLabels[domain] || domain}</strong><span>{items.map((item) => item.quantity && item.quantity > 1 ? `${item.name} ×${item.quantity}` : item.name).join(", ")}</span></div>)}
              </details>
              <span className="starter-preset-tradeoff">Особенность: {preset.tradeoffs}</span>
            </button>;
          })}
        </div>
        {presetError && <div className="alert">Не удалось загрузить стартовые профили: {presetError}</div>}
        {error && <div className="alert">{error}</div>}
        <button className="button button-primary create-button" disabled={busy || !presets.some((preset) => preset.id === selectedPreset)} title={busy ? "Дождитесь завершения текущего запроса." : !presets.length ? "Сначала загрузите каталог стартовых профилей." : undefined} onClick={() => onCreate(selectedPreset)}>＋ Создать сессию с выбранной комплектацией</button>
        <div className="form-divider"><span>или присоединиться</span></div>
        <label>ID игровой сессии<input value={sessionId} onChange={(event) => setSessionId(event.target.value)} placeholder="UUID сессии" /></label>
        <label>Ваше имя<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Например, Алексей" /></label>
        <label>Код приглашения<input value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} placeholder="Код от командира колонии" /></label>
        <button className="button button-secondary create-button" disabled={busy || !sessionId.trim() || !displayName.trim() || !inviteCode.trim()} title={busy ? "Дождитесь завершения текущего запроса." : !sessionId.trim() ? "Введите полный ID игровой сессии." : !displayName.trim() ? "Введите имя участника." : "Введите код приглашения."}
          onClick={() => void onJoin(sessionId.trim(), displayName.trim(), inviteCode.trim())}>Подключиться к сессии</button>
        <div className="login-foot">Локальная игровая сеть · Ares-1 · До 20 участников</div>
      </div>
    </div>
  </div>;
}

function Overview({ session, state, projects, events, isCommander, busy, onAdvance, onOpenProjects }: {
  session: SessionData | null; state: Record<string, unknown>; projects: Project[];
  events: EventMessage[]; isCommander: boolean; busy: boolean; onAdvance: () => Promise<void>; onOpenProjects: () => void;
}) {
  const activeProjects = projects.filter((project) => project.status === "executing");
  return <>
    <div className="stats-grid">
      <Metric label="КИСЛОРОД" value={quantity(numberAt(state, "oxygen_kg"))} unit="кг" detail="Резерв жизнеобеспечения" />
      <Metric label="ПОДГОТОВЛЕННАЯ ВОДА" value={money(numberAt(state, "prepared_water_kg"))} unit="кг" detail="Водный контур колонии" />
      <Metric label="ПИЩЕВЫЕ ЗАПАСЫ" value={quantity(numberAt(state, "dry_food_kg"))} unit="кг" detail="Сухие пищевые вещества" />
      <Metric label="БЮДЖЕТ" value={money(numberAt(state, "budget_credits"))} unit="кр." detail={`Свободно: ${money(session?.available_budget_credits || 0)} кр.`} />
    </div>
    <div className="section-grid">
      <section className="panel main-panel">
        <div className="panel-heading"><div><div className="eyebrow">COLONY STATUS</div><h2>Состояние систем</h2></div><div className="panel-actions"><span className="live-tag"><i /> LIVE</span>{isCommander && <button className="button button-primary small-button" disabled={busy || (session?.current_period || 0) >= (session?.settings?.period_count ?? 4)} title={busy ? "Дождитесь завершения текущего запроса." : (session?.current_period || 0) >= (session?.settings?.period_count ?? 4) ? "Все периоды кампании завершены." : undefined} onClick={() => void onAdvance()}>Завершить период →</button>}</div></div>
        <div className="system-row"><div className="system-icon">◈</div><div className="system-info"><strong>Энергетика</strong><span>Генерация и распределение мощности</span></div><div className="system-value">{quantity(numberAt(state, "power_generation_kw") || 72)} <small>кВт</small></div></div>
        <div className="system-row"><div className="system-icon">⌁</div><div className="system-info"><strong>Инфраструктура</strong><span>Введённые объекты колонии</span></div><div className="system-value">{Array.isArray(state.infrastructure) ? state.infrastructure.length : 0} <small>объектов</small></div></div>
        <div className="system-row"><div className="system-icon">⌬</div><div className="system-info"><strong>Технологии</strong><span>Освоенные технологические решения</span></div><div className="system-value">{Array.isArray(state.technologies) ? state.technologies.length : 0} <small>ед.</small></div></div>
        <div className="system-row"><div className="system-icon">♙</div><div className="system-info"><strong>Активные проекты</strong><span>Проекты в стадии исполнения</span></div><div className="system-value">{activeProjects.length} <small>из {projects.length}</small></div></div>
      </section>
      <section className="panel side-panel">
        <div className="panel-heading"><div><div className="eyebrow">PROJECT OFFICE</div><h2>Проекты</h2></div><button className="text-button" onClick={onOpenProjects}>Все проекты ↗</button></div>
        {projects.length === 0 ? <div className="empty-state"><span>▤</span><strong>Пока нет проектов</strong><p>Здесь появятся инициативы и ход их исполнения.</p></div> :
          projects.slice(0, 4).map((project) => <div className="compact-project" key={project.id}><div><strong>{project.title}</strong><span>{project.status} · {project.domain_codes.join(", ")}</span></div><b>{project.progress_periods}/{project.duration_periods}</b></div>)}
      </section>
    </div>
    <section className="panel event-panel">
      <div className="panel-heading"><div><div className="eyebrow">EVENT STREAM</div><h2>Последние изменения</h2></div><span className="muted small">WebSocket · PostgreSQL event log</span></div>
      {events.length === 0 ? <p className="muted">Ожидание событий игровой сессии.</p> : events.slice().reverse().map((event) =>
        <div className="event-row" key={event.sequence}><span className="event-seq">#{event.sequence}</span><strong>{event.event_type}</strong><span>{event.created_at ? new Date(event.created_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "—"}</span></div>)}
    </section>
  </>;
}

function Tutorial({ step, onStepChange, onNavigate, onCreateTrainingSession, onReturnToPreviousSession, hasPreviousSession, busy }: { step: number; onStepChange: (step: number) => void; onNavigate: (view: View) => void; onCreateTrainingSession: () => void; onReturnToPreviousSession: () => void; hasPreviousSession: boolean; busy: boolean }) {
  const steps: Array<{ title: string; explanation: string; task: string; view: View; action: string }> = [
    { title: "Осмотрите колонию", explanation: "Обзор — оперативная панель командира. Здесь видны запасы кислорода, воды и пищи, доступный бюджет, состояние инфраструктуры и активные проекты.", task: "Сравните запасы с рисками и определите, какая система требует внимания в первую очередь.", view: "overview", action: "Открыть обзор" },
    { title: "Познакомьтесь с экипажем", explanation: "Командир распределяет роли и зоны ответственности. Руководитель направления готовит проекты в своей области, финансовый руководитель проверяет бюджет, инженер помогает разрабатывать и исполнять проекты.", task: "Если играете с другими участниками, откройте список экипажа и назначьте хотя бы одному игроку направление. Для одиночного обучения этот шаг можно пропустить.", view: "people", action: "Открыть экипаж" },
    { title: "Создайте учебный проект", explanation: "Проект — основной способ менять инфраструктуру и производственные возможности колонии. У него есть тип, направления, стоимость, длительность, потребность в труде, энергии и материалах.", task: "Создайте тестовый проект: название «Учебный ремонт», тип «Ремонт», направление «Энергетика», бюджет 0, длительность 1 период, труд и энергия 0. Для учебного проекта можно оставить ожидаемый результат пустым.", view: "projects", action: "Открыть проектный офис" },
    { title: "Отправьте проект на согласование", explanation: "Отправка фиксирует версию проекта и резервирует указанный бюджет. После отправки параметры заявки проходят проверку сервером.", task: "Найдите «Учебный ремонт» и нажмите «Отправить на согласование».", view: "projects", action: "Перейти к проектам" },
    { title: "Примите решение", explanation: "Обычный проект согласуют уполномоченные руководители затронутых направлений; для платного проекта также требуется финансовое решение. Если командир сам является автором, он может утвердить проект командирским распоряжением с обязательным письменным обоснованием. Решение попадёт в журнал.", task: "Откройте согласование проекта. Для собственного проекта командира укажите обоснование не короче 10 символов.", view: "projects", action: "Открыть согласования" },
    { title: "Запустите исполнение", explanation: "Утверждение ещё не меняет инфраструктуру. Запуск проекта — отдельная команда: сервер повторно проверяет полномочия, материалы, трудовые и энергетические ограничения.", task: "После статуса «Согласован» нажмите «Запустить проект».", view: "projects", action: "Открыть проект" },
    { title: "Завершите игровой период", explanation: "Период охватывает 30 марсианских солов. При его завершении сервер пересчитывает потребление, производство, энергетику и прогресс активных проектов. Действие доступно командиру.", task: "Перейдите в обзор и нажмите «Завершить период». В учебной миссии достаточно одного перехода.", view: "overview", action: "Открыть обзор" },
    { title: "Подтвердите результат", explanation: "Когда прогресс проекта достигнет длительности, уполномоченный участник подтверждает завершение. Только после этого применяются заявленные эффекты проекта и освобождаются занятые ресурсы исполнения.", task: "Вернитесь в проектный офис, проверьте прогресс и нажмите «Подтвердить завершение».", view: "projects", action: "Открыть проектный офис" }
  ];
  const current = steps[step];
  return <section className="panel tutorial-panel">
    <div className="eyebrow">ARES-01 / TRAINING MISSION</div>
    <h2>Учебная миссия «Первая смена»</h2>
    <p className="muted tutorial-intro">Учебная миссия выполняется в отдельной сессии, чтобы не менять состояние вашей основной кампании. Сначала сохраните ID и код текущей сессии, затем создайте отдельную учебную сессию кнопкой ниже. Проект не требует бюджета, материалов или энергии.</p>
    <button className="button button-secondary" disabled={busy} title={busy ? "Дождитесь завершения текущего запроса." : "Создаст новую сессию Ares-1 для обучения; текущая сессия останется сохранённой на сервере."} onClick={onCreateTrainingSession}>Создать отдельную учебную сессию</button>
    {hasPreviousSession && <button className="button button-quiet" disabled={busy} title={busy ? "Дождитесь завершения текущего запроса." : "Вернуться в сохранённую основную сессию."} onClick={onReturnToPreviousSession}>Вернуться к предыдущей сессии</button>}
    <div className="tutorial-progress"><span style={{ width: `${((step + 1) / steps.length) * 100}%` }} /></div>
    <div className="tutorial-step-count">ШАГ {step + 1} ИЗ {steps.length}</div>
    <h3>{current.title}</h3><p>{current.explanation}</p>
    <div className="tutorial-task"><strong>Ваше задание</strong><p>{current.task}</p></div>
    <div className="tutorial-actions">
      <button className="button button-quiet" disabled={step === 0} title={step === 0 ? "Это первый шаг учебной миссии." : undefined} onClick={() => onStepChange(Math.max(0, step - 1))}>Назад</button>
      <button className="button button-secondary" onClick={() => onNavigate(current.view)}>{current.action}</button>
      <button className="button button-primary" disabled={step === steps.length - 1} title={step === steps.length - 1 ? "Учебная миссия завершена." : undefined} onClick={() => onStepChange(Math.min(steps.length - 1, step + 1))}>{step === steps.length - 1 ? "Завершено" : "Следующий шаг"}</button>
    </div>
    <p className="muted tutorial-foot">Подсказка: в обычной партии команда планирует проекты, согласует межведомственные решения, запускает исполнение и только затем завершает период. ИИ-ведущий — дополнительный помощник; базовый игровой цикл работает без внешней модели.</p>
  </section>;
}
function Metric({ label, value, unit, detail }: { label: string; value: string; unit: string; detail: string }) {
  return <div className="metric-card"><span>{label}</span><div><strong>{value}</strong><small>{unit}</small></div><p>{detail}</p></div>;
}

function ColonyMap({ assets }: { assets: Array<Record<string, unknown>> }) {
  const [activeLayer, setActiveLayer] = useState("all");
  const [selectedAsset, setSelectedAsset] = useState<Record<string, unknown> | null>(null);
  const kinds = Array.from(new Set(assets.map((asset) => String(asset.kind || "other")))).sort();
  const matchesLayer = (asset: Record<string, unknown>) =>
    activeLayer === "all" || String(asset.kind || "other") === activeLayer;
  const positioned = assets.filter((asset) => {
    const properties = asset.properties as Record<string, unknown> | undefined;
    return properties && typeof properties.x === "number" && typeof properties.y === "number"
      && properties.x >= 0 && properties.x <= 100 && properties.y >= 0 && properties.y <= 100;
  });
  const visible = positioned.filter(matchesLayer);
  const unplaced = assets.filter((asset) => !positioned.includes(asset) && matchesLayer(asset));
  return <div className="panel map-panel">
    <div className="map-toolbar"><div><div className="eyebrow">WORLD MODEL / SURFACE PLAN</div><h2>Инфраструктура Ares-1</h2><p className="map-instructions">Выберите слой и маркер объекта. Стартовая комплектация отображается в реестре и на схеме по зонам ответственности. Экономические эффекты отдельных стартовых объектов пока не рассчитываются движком; это учтённые объекты, а не уже подключённые производственные модели.</p></div><span className="layer-chip">◉ Слои по типу объекта</span></div>
    <div className="map-layers" aria-label="Слои карты">
      <button className={activeLayer === "all" ? "map-layer active" : "map-layer"} onClick={() => setActiveLayer("all")}>Все объекты</button>
      {kinds.map((kind) => <button key={kind} className={activeLayer === kind ? "map-layer active" : "map-layer"} onClick={() => setActiveLayer(kind)}>{kind}</button>)}
    </div>
    <div className="map-canvas">
      <div className="map-grid" /><div className="map-horizon" />
      <button className="map-base" type="button" title="Центральный узел колонии Ares-1" onClick={() => setSelectedAsset({ name: "Базовый модуль Ares-1", kind: "base", properties: { description: "Центральный узел сценария. Подробная геометрия базы пока не задана." } })}><span className="base-symbol">⌂</span><strong>ARES-1</strong><small>БАЗОВЫЙ МОДУЛЬ</small></button>
      {visible.map((asset, index) => {
        const properties = asset.properties as Record<string, unknown>;
        return <button type="button" className={selectedAsset?.id === asset.id ? "map-marker selected" : "map-marker"} key={String(asset.id)} style={{ left: `${properties.x}%`, top: `${properties.y}%` }} onClick={() => setSelectedAsset(asset)}><i />{String(asset.name || `Объект ${index + 1}`)}</button>;
      })}
      <div className="map-coordinate">SURFACE GRID · SCHEMATIC VIEW</div>
    </div>
    <div className="map-legend"><span><i className="legend-base" />Базовый модуль</span><span><i className="legend-asset" />Инфраструктурный объект</span><span className="muted">{visible.length} размещено · {unplaced.length} без координат</span></div>
    {selectedAsset && <section className="map-object-details"><div><span className="eyebrow">ВЫБРАННЫЙ ОБЪЕКТ</span><h3>{String(selectedAsset.name || "Объект без названия")}</h3><p>{String((selectedAsset.properties as Record<string, unknown> | undefined)?.description || "Описание для этого объекта пока не задано.")}</p><small>Тип: {String(selectedAsset.kind || "не указан")} · Состояние: {String((selectedAsset.properties as Record<string, unknown> | undefined)?.status ?? "не задано")}</small></div><button className="button button-quiet small-button" onClick={() => setSelectedAsset(null)}>Снять выбор</button></section>}
    {unplaced.length > 0 && <div className="unplaced"><strong>Объекты выбранного слоя без координат</strong><p>Для этих объектов геометрия пока не задана. Они остаются в реестре и не получают условные координаты.</p>{unplaced.map((asset) => <span key={String(asset.id)}>{String(asset.name)} · {String(asset.kind || "other")}</span>)}</div>}
    {assets.length === 0 && <div className="empty-map-note">Инфраструктурные объекты появятся после завершения соответствующих проектов.</div>}
  </div>;
}

function ProjectList({ projects, auth, player, infrastructure, resourceCatalog, onRefresh }: {
  projects: Project[]; auth: Auth; player: Player | undefined; infrastructure: Array<Record<string, unknown>>;
  resourceCatalog: Record<string, { state_key?: string; unit?: string; precision_digits?: number }>;
  onRefresh: () => Promise<void>;
}) {
  const [formOpen, setFormOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [domains, setDomains] = useState<string[]>(["life_support"]);
  const [projectType, setProjectType] = useState("construction");
  const [budget, setBudget] = useState("0");
  const [duration, setDuration] = useState("1");
  const [laborFte, setLaborFte] = useState("0");
  const [energy, setEnergy] = useState("0");
  const [resourceInputs, setResourceInputs] = useState<Record<string, string>>({});
  const [resourceOutputs, setResourceOutputs] = useState<Record<string, string>>({});
  const [generationDelta, setGenerationDelta] = useState("0");
  const [loadDelta, setLoadDelta] = useState("0");
  const [assetName, setAssetName] = useState("");
  const [assetKind, setAssetKind] = useState("infrastructure");
  const [customAssetKind, setCustomAssetKind] = useState("");
  const [assetX, setAssetX] = useState("");
  const [assetY, setAssetY] = useState("");
  const [catalogEntities, setCatalogEntities] = useState<CatalogEntity[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [selectedCatalogIds, setSelectedCatalogIds] = useState<string[]>([]);
  const [entityFormOpen, setEntityFormOpen] = useState(false);
  const [newEntityType, setNewEntityType] = useState("infrastructure");
  const [newEntityName, setNewEntityName] = useState("");
  const [newEntityDescription, setNewEntityDescription] = useState("");
  const [newEntityDefinition, setNewEntityDefinition] = useState('{"kind":"custom_unit","build":{"budget_credits":100,"duration_periods":2,"labor_fte":1,"energy_kwh_per_period":5,"resource_requirements":{}},"operation":{"power_generation_kw":0,"power_load_kw":0.5,"resource_inputs_per_period":{},"resource_outputs_per_period":{},"operating_cost_credits_per_period":1}}');
  const [entityProposalError, setEntityProposalError] = useState("");
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState("");
  const roleSet = new Set(player?.roles || []);
  const canPropose = ["colony_commander", "domain_lead", "engineer"].some((role) => roleSet.has(role));
  useEffect(() => {
    let active = true;
    request<{ entities: CatalogEntity[] }>(API + "/sessions/" + auth.sessionId + "/entity-catalog", auth.token)
      .then((result) => { if (active) { setCatalogEntities(result.entities); setCatalogError(""); } })
      .catch((reason: Error) => { if (active) setCatalogError(reason.message); });
    return () => { active = false; };
  }, [auth.sessionId, auth.token]);
  const labels: Record<string, string> = { draft: "Черновик", submitted: "На согласовании", approved: "Согласован", executing: "В исполнении", completed: "Завершён", rejected: "Отклонён", cancelled: "Отменён" };
  const domainLabels: Record<string, string> = {
    geology: "Геология", industry: "Промышленность", energy: "Энергетика",
    life_support: "Жизнеобеспечение", construction: "Строительство",
    agriculture: "Агрокомплекс", medicine: "Медицина", social: "Социальное развитие",
    education: "Образование", science: "Наука", planetary: "Планетология",
    transport: "Транспорт", administration: "Администрация", safety: "Безопасность",
    communications: "Связь", external_relations: "Внешние связи"
  };
  const resourceLabels: Record<string, string> = {
    ...Object.fromEntries(Object.entries(resourceCatalog).map(([key, item]) => [key, `${key} (${item.unit || "ед."})`])),
    oxygen: "Кислород, кг", food_solids: "Сухие пищевые вещества, кг",
    usable_water: "Подготовленная вода, кг", raw_ice: "Сырой лёд, кг"
  };
  const existingAssetKinds = Array.from(new Set(infrastructure.map((asset) => String(asset.kind || "")).filter(Boolean))).sort();
  const changeDomain = (code: string, checked: boolean) => setDomains((current) =>
    checked ? [...current, code] : current.filter((item) => item !== code)
  );
  const mutate = async (project: Project, action: string, extra: Record<string, unknown> = {}) => {
    let actionData = extra;
    if (action === "approvals" && extra.decision === "approve" && project.author_player_id === auth.playerId && roleSet.has("colony_commander")) {
      const justification = window.prompt("Командирское распоряжение по собственному проекту. Укажите обоснование (не менее 10 символов):");
      if (!justification || justification.trim().length < 10) {
        setActionError("Командирское распоряжение не отправлено: требуется обоснование не короче 10 символов.");
        return;
      }
      actionData = { ...extra, comment: justification.trim() };
    }
    setSaving(true); setActionError("");
    try {
      await request(`${API}/sessions/${auth.sessionId}/projects/${project.id}/${action}`, auth.token, {
        method: "POST", body: JSON.stringify({ expected_version: project.version, ...actionData })
      });
      await onRefresh();
    } catch (reason) { setActionError((reason as Error).message); }
    finally { setSaving(false); }
  };
  const selectedCatalogEntities = catalogEntities.filter((entity) => selectedCatalogIds.includes(entity.id));
  const toggleCatalogEntity = (entity: CatalogEntity, checked: boolean) => {
    setSelectedCatalogIds((current) => checked
      ? [...current, entity.id]
      : current.filter((id) => id !== entity.id));
    if (checked) {
      setDomains((current) => Array.from(new Set([...current, ...entity.domain_codes])));
      const build = entity.build || entity.research || {};
      setBudget((current) => String(Math.max(Number(current) || 0, Number(build.budget_credits) || 0)));
      setDuration((current) => String(Math.max(Number(current) || 1, Number(build.duration_periods) || 1)));
      setLaborFte((current) => String(Math.max(Number(current) || 0, Number(build.labor_fte) || 0)));
      setEnergy((current) => String(Math.max(Number(current) || 0, Number(build.energy_kwh_per_period) || 0)));
      const requirements = (build.resource_requirements || {}) as Record<string, number>;
      setResourceInputs((current) => {
        const next = { ...current };
        Object.entries(requirements).forEach(([key, value]) => {
          next[key] = String(Math.max(Number(next[key]) || 0, Number(value) || 0));
        });
        return next;
      });
    }
  };
  const proposeNewEntity = async () => {
    setEntityProposalError("");
    let definition: Record<string, unknown>;
    try {
      definition = JSON.parse(newEntityDefinition) as Record<string, unknown>;
    } catch {
      setEntityProposalError("Определение сущности должно быть корректным JSON-объектом.");
      return;
    }
    if (domains.length === 0) {
      setEntityProposalError("Выберите хотя бы одну зону ответственности.");
      return;
    }
    setSaving(true);
    try {
      await request(API + "/sessions/" + auth.sessionId + "/entity-catalog/proposals", auth.token, {
        method: "POST",
        body: JSON.stringify({
          entity_type: newEntityType, name: newEntityName,
          description: newEntityDescription, domain_codes: domains,
          definition, source_text: "Создано игроком через проектный офис."
        })
      });
      setNewEntityName("");
      setNewEntityDescription("");
      setEntityFormOpen(false);
      setEntityProposalError("Предложение отправлено на согласование. После одобрения оно появится в каталоге.");
    } catch (reason) {
      setEntityProposalError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const createProject = async () => {
    const requirements = Object.fromEntries(Object.entries(resourceInputs)
      .filter(([, value]) => value.trim() !== "" && Number(value) > 0)
      .map(([key, value]) => [key, Number(value)]));
    const outputs = Object.fromEntries(Object.entries(resourceOutputs)
      .filter(([, value]) => value.trim() !== "" && Number(value) > 0)
      .map(([key, value]) => [key, Number(value)]));
    const expected: Record<string, unknown> = {};
    if (Object.keys(outputs).length) expected.resource_outputs_per_period = outputs;
    if (Number(generationDelta) > 0) expected.power_generation_kw_delta = Number(generationDelta);
    if (Number(loadDelta) > 0) expected.power_load_kw_delta = Number(loadDelta);
    const selectedKind = assetKind === "__custom__" ? customAssetKind.trim() : assetKind.trim();
    if (assetName.trim() && selectedKind) {
      const properties: Record<string, unknown> = { condition: 1 };
      if (assetX.trim() !== "" && assetY.trim() !== "") { properties.x = Number(assetX); properties.y = Number(assetY); }
      expected.infrastructure = [{ name: assetName.trim(), kind: selectedKind, properties }];
    }
    setSaving(true); setActionError("");
    try {
      await request(`${API}/sessions/${auth.sessionId}/projects`, auth.token, {
        method: "POST",
        body: JSON.stringify({
          title, description, project_type: projectType, domain_codes: domains,
          budget_credits: Number(budget), duration_periods: Number(duration),
          labor_fte: Number(laborFte), energy_kwh_per_period: Number(energy),
          resource_requirements: requirements, expected_result: expected,
          catalog_entity_ids: selectedCatalogIds
        })
      });
      setTitle(""); setDescription(""); setBudget("0"); setDuration("1");
      setLaborFte("0"); setEnergy("0"); setResourceInputs({}); setResourceOutputs({});
      setGenerationDelta("0"); setLoadDelta("0"); setAssetName(""); setAssetKind(existingAssetKinds[0] || "infrastructure"); setCustomAssetKind(""); setAssetX(""); setAssetY("");
      setSelectedCatalogIds([]);
      setFormOpen(false); await onRefresh();
    } catch (reason) { setActionError((reason as Error).message); }
    finally { setSaving(false); }
  };
  const validNumber = (value: string, maximum = 1000000) =>
    value.trim() !== "" && Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= maximum;
  const describeEffects = (effect: Record<string, unknown>) => {
    const details: string[] = [];
    const outputs = effect.resource_outputs_per_period as Record<string, number> | undefined;
    if (outputs) Object.entries(outputs).forEach(([key, value]) => details.push(`${resourceLabels[key] || key}: +${quantity(value, 2)}/период`));
    if (Number(effect.power_generation_kw_delta) > 0) details.push(`Генерация +${quantity(Number(effect.power_generation_kw_delta), 2)} кВт`);
    if (Number(effect.power_load_kw_delta) > 0) details.push(`Нагрузка +${quantity(Number(effect.power_load_kw_delta), 2)} кВт`);
    const assets = effect.infrastructure as Array<{ name?: string }> | undefined;
    if (assets) assets.forEach((asset) => details.push(`Объект: ${asset.name || "без названия"}`));
    return details.join(" · ") || "Не задан";
  };
  const selectedAssetKind = assetKind === "__custom__" ? customAssetKind.trim() : assetKind.trim();
  const invalidAsset = Boolean(assetName.trim()) && (!selectedAssetKind || ((assetX.trim() === "") !== (assetY.trim() === "")) || (assetX.trim() !== "" && (!validNumber(assetX, 100) || !validNumber(assetY, 100))));
  const invalidResourceValues = [...Object.values(resourceInputs), ...Object.values(resourceOutputs)].some((value) => value.trim() !== "" && !validNumber(value));
  return <>
    <div className="project-toolbar">
      <div className="muted small">{projects.length} инициатив · сервер проверяет полномочия и доступность ресурсов</div>
      {canPropose && <button className="button button-primary" onClick={() => setFormOpen((value) => !value)}>{formOpen ? "Закрыть форму" : "＋ Новый проект"}</button>}
    </div>
    {actionError && <div className="alert">{actionError}<button onClick={() => setActionError("")}>×</button></div>}
    {formOpen && <section className="panel project-form">
      <div className="eyebrow">NEW PROJECT PROPOSAL</div><h2>Новая инициатива</h2>
      <div className="form-grid">
        <label>Название проекта<input value={title} onChange={(event) => setTitle(event.target.value)} minLength={3} maxLength={160} placeholder="Например, резервный контур воды" /></label>
        <label>Тип проекта<select value={projectType} onChange={(event) => setProjectType(event.target.value)}>{[["construction","Строительство"],["production","Производство"],["repair","Ремонт"],["research","Исследование"],["exploration","Разведка"],["procurement","Закупка"],["infrastructure","Инфраструктура"],["other","Прочее"]].map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>Бюджет, игровые кредиты<input type="number" min="0" max="1000000000" step="0.1" value={budget} onChange={(event) => setBudget(event.target.value)} /><small>Общая стоимость проекта. Резервируется при запуске и проверяется по доступному бюджету колонии.</small></label>
        <label>Длительность, игровые периоды<input type="number" min="1" max="100" step="1" value={duration} onChange={(event) => setDuration(event.target.value)} /><small>Один период сценария Ares-1 равен 30 солам. Срок отсчитывается после запуска.</small></label>
        <label>Трудозатраты, FTE<input type="number" min="0" max="1000000" step="0.1" value={laborFte} onChange={(event) => setLaborFte(event.target.value)} /><small>Средняя занятость персонала проекта в эквиваленте полной ставки на период исполнения.</small></label>
        <label>Энергия за период, кВт·ч<input type="number" min="0" max="1000000" step="0.1" value={energy} onChange={(event) => setEnergy(event.target.value)} /><small>Электроэнергия, которую проект должен получить за каждый период выполнения.</small></label>
      </div>
      <div className="form-section"><strong>Направления проекта</strong><div className="check-grid">{Object.entries(domainLabels).map(([code, label]) => <label className="check-option" key={code}><input type="checkbox" checked={domains.includes(code)} onChange={(event) => changeDomain(code, event.target.checked)} />{label}</label>)}</div></div>
      <div className="form-section">
        <strong>Готовые объекты и технологии из каталога</strong>
        <p className="field-help">Выберите типовые сущности, которые проект должен построить, установить или исследовать. Сервер автоматически добавит их характеристики и экономические эффекты в результат проекта. Для каждого типа учитываются минимальные бюджет, срок, труд и энергия.</p>
        {catalogError && <p className="field-help">Каталог недоступен: {catalogError}</p>}
        <div className="catalog-entity-list">
          {catalogEntities.map((entity) => {
            const build = entity.build || entity.research || {};
            const selected = selectedCatalogIds.includes(entity.id);
            return <label className="catalog-entity-option" key={entity.id}>
              <input type="checkbox" checked={selected} onChange={(event) => toggleCatalogEntity(entity, event.target.checked)} />
              <span><strong>{entity.name}</strong><small>{entity.entity_type === "technology" ? "Технология" : entity.entity_type === "vehicle" ? "Транспорт" : "Инфраструктура"} · {entity.domain_codes.map((code) => domainLabels[code] || code).join(", ")}</small><small>{entity.description}</small><small>Минимум: {money(Number(build.budget_credits) || 0)} кр. · {Number(build.duration_periods) || 1} пер. · {quantity(Number(build.labor_fte) || 0, 1)} FTE · {quantity(Number(build.energy_kwh_per_period) || 0, 1)} кВт·ч/пер.</small></span>
            </label>;
          })}
        </div>
        {selectedCatalogEntities.length > 0 && <p className="field-help">Выбрано сущностей: {selectedCatalogEntities.length}. Их зоны ответственности добавлены к проекту; параметры проекта должны покрывать суммарные требования.</p>}
        <button type="button" className="button button-secondary" onClick={() => setEntityFormOpen((value) => !value)}>{entityFormOpen ? "Закрыть создание типа" : "＋ Предложить новый тип сущности"}</button>
        {entityFormOpen && <div className="entity-proposal-form">
          <label>Категория<select value={newEntityType} onChange={(event) => setNewEntityType(event.target.value)}><option value="infrastructure">Инфраструктура</option><option value="equipment">Оборудование</option><option value="vehicle">Транспорт</option><option value="technology">Технология</option><option value="process">Процесс</option><option value="procedure">Процедура</option></select></label>
          <label>Название<input value={newEntityName} onChange={(event) => setNewEntityName(event.target.value)} maxLength={160} placeholder="Название нового типа" /></label>
          <label>Описание<textarea value={newEntityDescription} onChange={(event) => setNewEntityDescription(event.target.value)} rows={2} maxLength={5000} placeholder="Назначение, ограничения и принцип работы" /></label>
          <label>Техническое определение (JSON)<textarea value={newEntityDefinition} onChange={(event) => setNewEntityDefinition(event.target.value)} rows={8} /></label>
          <p className="field-help">Определение проходит серверную проверку. Новая сущность не попадёт в общий каталог до согласования командиром или руководителем всех затронутых направлений.</p>
          {entityProposalError && <p className="field-help">{entityProposalError}</p>}
          <button type="button" className="button button-primary" disabled={saving || newEntityName.trim().length < 2 || !newEntityDescription.trim()} onClick={() => void proposeNewEntity()}>Отправить тип на согласование</button>
        </div>}
      </div>
      <div className="form-section"><strong>Материальные требования на старте проекта</strong><p className="field-help">Количество ресурсов, которое будет списано со складов при запуске проекта. Указывайте значения в единицах, показанных рядом с ресурсом. Пустое поле означает, что ресурс не требуется.</p><div className="form-grid">{Object.entries(resourceLabels).map(([code, label]) => <label key={code}>{label}<input type="number" min="0" step="0.01" value={resourceInputs[code] || ""} onChange={(event) => setResourceInputs((current) => ({ ...current, [code]: event.target.value }))} placeholder="0" /></label>)}</div></div>
      <div className="form-section"><strong>Ожидаемый дополнительный выпуск за период</strong><p className="field-help">Дополнительное количество ресурса, которое проект начнёт производить за каждый игровой период после завершения. Это эффект проекта, а не выпуск во время строительства.</p><div className="form-grid">{Object.entries(resourceLabels).map(([code, label]) => <label key={code}>{label}<input type="number" min="0" step="0.01" value={resourceOutputs[code] || ""} onChange={(event) => setResourceOutputs((current) => ({ ...current, [code]: event.target.value }))} placeholder="0" /></label>)}</div></div>
      <div className="form-grid">
        <label>Дополнительная генерация, кВт<input type="number" min="0" step="0.1" value={generationDelta} onChange={(event) => setGenerationDelta(event.target.value)} /></label>
        <label>Дополнительная нагрузка, кВт<input type="number" min="0" step="0.1" value={loadDelta} onChange={(event) => setLoadDelta(event.target.value)} /></label>
        <label>Новый объект инфраструктуры<input value={assetName} onChange={(event) => setAssetName(event.target.value)} maxLength={120} placeholder="Например, резервный резервуар" /><small>Конкретный экземпляр, который появится на карте после завершения проекта.</small></label>
        <label>Тип объекта<select value={assetKind} onChange={(event) => setAssetKind(event.target.value)}>{Array.from(new Set([...existingAssetKinds, "habitat", "power_unit", "water_tank", "greenhouse", "industrial_unit", "infrastructure"])).map((kind) => <option key={kind} value={kind}>{kind}</option>)}<option value="__custom__">＋ Добавить новый тип…</option></select>{assetKind === "__custom__" && <input value={customAssetKind} onChange={(event) => setCustomAssetKind(event.target.value)} maxLength={80} placeholder="Новый идентификатор типа, например water_recycler" />}<small>Выберите существующий тип или добавьте новый. Тип — категория; название выше — отдельный объект.</small></label>
        <label>Координата X, % от ширины карты<input type="number" min="0" max="100" step="0.1" value={assetX} onChange={(event) => setAssetX(event.target.value)} placeholder="Не задана" /></label>
        <label>Координата Y, % от высоты карты<input type="number" min="0" max="100" step="0.1" value={assetY} onChange={(event) => setAssetY(event.target.value)} placeholder="Не задана" /><small>Обе координаты необязательны. Если не заданы, объект останется в списке «без координат».</small></label>
      </div>
      <label className="form-wide">Описание<textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={5000} rows={3} placeholder="Опишите цель, ограничения и ожидаемый результат проекта." /></label>
      <div className="form-foot"><span className="muted">Материалы списываются при запуске. Труд и энергия резервируются на период исполнения. Эффекты вводятся после подтверждения завершения.</span><button className="button button-primary" disabled={saving || title.trim().length < 3 || !description.trim() || domains.length === 0 || !validNumber(budget, 1000000000) || !validNumber(duration, 100) || !Number.isInteger(Number(duration)) || Number(duration) < 1 || !validNumber(laborFte) || !validNumber(energy) || !validNumber(generationDelta) || !validNumber(loadDelta) || invalidAsset || invalidResourceValues} title={saving ? "Сохраняется другой проектный запрос." : "Заполните название, описание и направление; проверьте бюджет, длительность, труд, энергию и материальные значения."} onClick={() => void createProject()}>Создать черновик</button></div>
    </section>}
    <div className="project-grid">{projects.length === 0 ? <div className="panel empty-state"><span>▤</span><strong>Проектов пока нет</strong><p>Создайте первую инициативу в проектном офисе.</p></div> : projects.map((project) =>
      <article className="panel project-card" key={project.id}>
        <div className="project-card-top"><span className="eyebrow">{project.project_type.toUpperCase()}</span><span className={`status status-${project.status}`}>{labels[project.status] || project.status}</span></div>
        <h3>{project.title}</h3><p>{project.description}</p>
        <div className="project-meta"><span>{project.domain_codes.map((code) => domainLabels[code] || code).join(" · ")}</span><strong>{money(project.budget_credits)} кр.</strong></div>
        <div className="project-detail-grid">
          <span>Труд: <b>{quantity(project.labor_fte || 0, 2)} FTE</b></span>
          <span>Энергия: <b>{quantity(project.energy_kwh_per_period || 0, 1)} кВт·ч/период</b></span>
          <span className="project-data-line">Материалы при запуске: <b>{Object.entries(project.resource_requirements || {}).map(([key, value]) => `${resourceLabels[key] || key}: ${quantity(value, 2)}`).join(" · ") || "Не заданы"}</b></span>
          <span className="project-data-line">Результат после завершения: <b>{describeEffects(project.expected_result || {})}</b></span>
        </div>        {project.approvals && project.approvals.length > 0 && <div className="approval-history">
          <strong>История решений</strong>
          {project.approvals.map((approval, index) => <p key={index}>
            {approval.approval_mode === "commander_order" ? "Командирское распоряжение" : "Согласование"} · {approval.decision === "approve" ? "одобрено" : "отклонено"}
            {approval.comment ? ` — ${approval.comment}` : ""}
          </p>)}
        </div>}
        <div className="progress-track"><i style={{ width: `${Math.min(100, project.progress_periods / Math.max(1, project.duration_periods) * 100)}%` }} /></div>
        <div className="progress-caption"><span>Ход исполнения</span><b>{project.progress_periods} / {project.duration_periods} периодов</b></div>
        <div className="project-actions">
          {project.status === "draft" && (project.author_player_id === auth.playerId || roleSet.has("colony_commander")) && <button disabled={saving} title={saving ? "Дождитесь завершения текущего действия." : undefined} onClick={() => void mutate(project, "submit")}>Отправить на согласование</button>}
          {project.status === "submitted" && ["colony_commander","domain_lead","finance_lead"].some((role) => roleSet.has(role)) && <>
            <button disabled={saving} title={saving ? "Дождитесь завершения текущего действия." : project.author_player_id === auth.playerId && roleSet.has("colony_commander") ? "Потребуется письменное обоснование командирского распоряжения." : undefined} onClick={() => void mutate(project, "approvals", { decision: "approve" })}>Согласовать</button>
            <button className="action-muted" disabled={saving} title={saving ? "Дождитесь завершения текущего действия." : undefined} onClick={() => void mutate(project, "approvals", { decision: "reject" })}>Отклонить</button>
          </>}
          {project.status === "approved" && ["colony_commander","domain_lead","engineer"].some((role) => roleSet.has(role)) && <button disabled={saving} title={saving ? "Дождитесь завершения текущего действия." : undefined} onClick={() => void mutate(project, "execute")}>Запустить проект</button>}
          {project.status === "executing" && project.progress_periods >= project.duration_periods && ["colony_commander","domain_lead","engineer"].some((role) => roleSet.has(role)) && <button disabled={saving} title={saving ? "Дождитесь завершения текущего действия." : undefined} onClick={() => void mutate(project, "complete")}>Подтвердить завершение</button>}
          {["draft","submitted","approved"].includes(project.status) && (project.author_player_id === auth.playerId || roleSet.has("colony_commander")) && <button className="action-muted" disabled={saving} title={saving ? "Дождитесь завершения текущего действия." : undefined} onClick={() => void mutate(project, "cancel")}>Отменить</button>}
        </div>
      </article>)}</div>
  </>;
}

function People({ players, technologies, auth, onRefresh }: {
  players: Player[]; technologies: Array<Record<string, unknown>>; auth: Auth;
  onRefresh: () => Promise<void>;
}) {
  const [editingPlayer, setEditingPlayer] = useState<string | null>(null);
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [selectedDomains, setSelectedDomains] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const roleLabels: Record<string, string> = {
    colony_commander: "Командир колонии", domain_lead: "Руководитель направления",
    finance_lead: "Финансовый руководитель", engineer: "Инженер", player: "Участник"
  };
  const domainLabels: Record<string, string> = {
    geology: "Геология", industry: "Промышленность", energy: "Энергетика",
    life_support: "Жизнеобеспечение", construction: "Строительство",
    agriculture: "Агрокомплекс", medicine: "Медицина", social: "Социальное развитие",
    education: "Образование", science: "Наука", planetary: "Планетология",
    transport: "Транспорт", administration: "Администрация", safety: "Безопасность",
    communications: "Связь", external_relations: "Внешние связи"
  };
  const isCommander = players.find((item) => item.id === auth.playerId)?.roles.includes("colony_commander") || false;
  const beginEdit = (player: Player) => {
    setEditingPlayer(player.id); setSelectedRoles(player.roles); setSelectedDomains(player.responsibility_domains); setError("");
  };
  const toggle = (items: string[], value: string, checked: boolean, setter: (next: string[]) => void) =>
    setter(checked ? [...items, value] : items.filter((item) => item !== value));
  const saveRoles = async () => {
    if (!editingPlayer) return;
    const needsDomains = selectedRoles.includes("domain_lead") || selectedRoles.includes("engineer");
    if (needsDomains && selectedDomains.length === 0) {
      setError("Для руководителя направления и инженера необходимо назначить хотя бы одно направление.");
      return;
    }
    setSaving(true); setError("");
    try {
      await request(`${API}/sessions/${auth.sessionId}/players/${editingPlayer}/roles`, auth.token, {
        method: "PUT", body: JSON.stringify({
          roles: selectedRoles,
          responsibility_domains: needsDomains ? selectedDomains : []
        })
      });
      setEditingPlayer(null); await onRefresh();
    } catch (reason) { setError((reason as Error).message); }
    finally { setSaving(false); }
  };
  return <div className="people-layout"><section className="panel">
    <div className="panel-heading"><div><div className="eyebrow">CREW MANIFEST</div><h2>Участники экспедиции</h2></div><span className="count-chip">{players.length} / 20</span></div>
    {error && <div className="alert">{error}<button onClick={() => setError("")}>×</button></div>}
    {players.map((player) => <div className="person-card" key={player.id}>
      <div className="person-row"><div className="person-avatar">{player.display_name.slice(0, 1)}</div><div className="person-details"><strong>{player.display_name}</strong><span>{player.roles.map((role) => roleLabels[role] || role).join(", ")}</span><small>{player.responsibility_domains.map((code) => domainLabels[code] || code).join(" · ") || "Без закреплённых направлений"}</small></div>
      {isCommander && <button className="button button-quiet role-edit-button" onClick={() => editingPlayer === player.id ? setEditingPlayer(null) : beginEdit(player)}>{editingPlayer === player.id ? "Закрыть" : "Полномочия"}</button>}</div>
      {editingPlayer === player.id && <div className="role-editor">
        <strong>Роли участника</strong><div className="check-grid role-check-grid">{Object.entries(roleLabels).map(([code, label]) => <label className="check-option" key={code}><input type="checkbox" checked={selectedRoles.includes(code)} onChange={(event) => toggle(selectedRoles, code, event.target.checked, setSelectedRoles)} />{label}</label>)}</div>
        <strong>Ответственные направления</strong><div className="check-grid">{Object.entries(domainLabels).map(([code, label]) => <label className="check-option" key={code}><input type="checkbox" checked={selectedDomains.includes(code)} onChange={(event) => toggle(selectedDomains, code, event.target.checked, setSelectedDomains)} />{label}</label>)}</div>
        <div className="form-foot"><span className="muted">Назначение сохраняется на сервере. В сессии должен оставаться хотя бы один командир.</span><button className="button button-primary" disabled={saving || selectedRoles.length === 0} title={saving ? "Дождитесь завершения сохранения." : selectedRoles.length === 0 ? "Назначьте участнику хотя бы одну роль." : undefined} onClick={() => void saveRoles()}>Сохранить полномочия</button></div>
      </div>}
    </div>)}
  </section><section className="panel"><div className="eyebrow">RESEARCH REGISTER</div><h2>Технологии</h2>{technologies.length === 0 ? <p className="muted">Исследовательские результаты пока не зарегистрированы.</p> : technologies.map((technology) => <div className="technology-row" key={String(technology.id)}><strong>{String(technology.name)}</strong><span>Уровень {String(technology.level)}</span></div>)}</section></div>;
}
