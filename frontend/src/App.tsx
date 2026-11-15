import { useCallback, useEffect, useMemo, useState } from "react";

type Auth = { sessionId: string; token: string; playerId: string; displayName: string; joinCode?: string };
type SessionData = {
  id: string;
  scenario_id: string;
  current_period: number;
  available_budget_credits: number;
  reserved_budget_credits: number;
  state: Record<string, unknown>;
  events?: Array<{ sequence: number; type: string; payload: Record<string, unknown>; created_at?: string | null }>;
};
type Player = { id: string; display_name: string; roles: string[]; responsibility_domains: string[] };
type Project = {
  id: string; author_player_id: string; version: number; title: string; description: string; project_type: string; status: string;
  domain_codes: string[]; budget_credits: number; progress_periods: number;
  duration_periods: number; labor_fte: number; energy_kwh_per_period: number;
  resource_requirements: Record<string, number>; expected_result: Record<string, unknown>;
  approvals?: Array<{ decision: string }>;
};
type EventMessage = { sequence: number; event_type: string; payload: Record<string, unknown>; created_at?: string | null };
type View = "overview" | "map" | "projects" | "people";

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
  const [session, setSession] = useState<SessionData | null>(null);
  const [players, setPlayers] = useState<Player[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [events, setEvents] = useState<EventMessage[]>([]);
  const [view, setView] = useState<View>("overview");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
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
      socket.onopen = () => socket?.send(JSON.stringify({ type: "authenticate", player_token: auth.token }));
      socket.onmessage = (message) => {
        const data = JSON.parse(message.data) as { type: string; last_sequence?: number; sequence?: number; event_type?: string; payload?: Record<string, unknown> };
        if (data.type === "snapshot") {
          setConnected(true);
          void refresh().catch((reason: Error) => setError(reason.message));
        } else if (data.type === "event" && typeof data.sequence === "number") {
          setEvents((previous) => [...previous, {
            sequence: data.sequence!, event_type: data.event_type || "session.updated", payload: data.payload || {}, created_at: data.created_at
          }].slice(-12));
          void refresh().catch((reason: Error) => setError(reason.message));
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (active) reconnectTimer = window.setTimeout(() => setRetry((value) => value + 1), 1500);
      };
      socket.onerror = () => setConnected(false);
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
    setAuth(null);
    setSession(null);
    setConnected(false);
    setEvents([]);
  };

  const createSession = async () => {
    setBusy(true); setError("");
    try {
      const result = await request<SessionData & { owner_token: string; owner_player_id: string; join_code: string }>(
        `${API}/sessions`, undefined, { method: "POST", body: JSON.stringify({ scenario_id: "ares-01" }) }
      );
      const next: Auth = {
        sessionId: result.id, token: result.owner_token, playerId: result.owner_player_id,
        displayName: "Командир колонии", joinCode: result.join_code
      };
      sessionStorage.setItem("marscolony.auth", JSON.stringify(next));
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
          <span className={connected ? "connection online" : "connection"}><i />{connected ? "Синхронизация активна" : "Подключение…"}</span>
          {!screenMode && <button className="button button-quiet" onClick={leave}>Выйти</button>}
        </div>
      </header>

      <main className="workspace">
        <aside className="sidebar">
          <div className="session-label">ТЕКУЩАЯ СЕССИЯ</div>
          <div className="session-id">{auth.sessionId.slice(0, 8).toUpperCase()}</div>
          <div className="nav-list">
            {([
              ["overview", "Обзор", "◫"], ["map", "Карта колонии", "⌖"],
              ["projects", "Проекты", "▤"], ["people", "Участники", "♙"]
            ] as Array<[View, string, string]>).map(([key, label, icon]) =>
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
              <div className="eyebrow">ARES-01 / MARS OPERATIONS</div>
              <h1>{view === "overview" ? "Центр управления" : view === "map" ? "Карта колонии" : view === "projects" ? "Проектный офис" : "Состав экспедиции"}</h1>
              <p>{view === "overview" ? "Оперативная картина состояния базы и ход экспедиции." : view === "map" ? "Инфраструктура и тематические объекты колонии." : view === "projects" ? "Инициативы, согласования и ход исполнения." : "Участники сессии и распределение ответственности."}</p>
            </div>
            <div className="period-chip"><span>ТЕКУЩИЙ ПЕРИОД</span><strong>{session?.current_period ?? "—"} <small>/ 4</small></strong></div>
          </div>

          {error && <div className="alert">{error}<button onClick={() => setError("")}>×</button></div>}

          {view === "overview" && <Overview session={session} state={state} projects={projects} events={events} isCommander={Boolean(commander?.roles.includes("colony_commander"))} busy={busy} onAdvance={advancePeriod} onOpenProjects={() => setView("projects")} />}
          {view === "map" && <ColonyMap assets={infrastructure} />}
          {view === "projects" && <ProjectList projects={projects} auth={auth} player={commander} infrastructure={infrastructure} onRefresh={refresh} />}
          {view === "people" && <People players={players} technologies={technologies} auth={auth} onRefresh={refresh} />}

          {auth.joinCode && <div className="invite-strip"><div><span>КОД ПРИГЛАШЕНИЯ</span><strong>{auth.joinCode}</strong></div><button className="button button-quiet" onClick={() => navigator.clipboard.writeText(auth.joinCode || "")}>Копировать</button></div>}
        </section>
      </main>
    </div>
  );
}

function Login({ busy, error, onCreate, onJoin }: {
  busy: boolean; error: string;
  onCreate: () => void;
  onJoin: (sessionId: string, displayName: string, inviteCode: string) => Promise<void>;
}) {
  const [sessionId, setSessionId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  return <div className="login-page">
    <div className="login-aside">
      <div className="brand"><span className="brand-mark">M</span><span>MARS<span className="brand-light">COLONY</span></span></div>
      <div className="login-art"><div className="planet" /><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="base-glow" /></div>
      <div className="login-caption"><div className="eyebrow">HUMANITY / NEXT FRONTIER</div><h1>Новая глава<br />начинается здесь.</h1><p>Единый центр управления марсианской экспедицией. Решения, ресурсы и люди — в одной системе.</p></div>
      <span className="aside-foot">ARES PROGRAM · MARS COLONY SIMULATION</span>
    </div>
    <div className="login-panel">
      <div className="login-form-wrap">
        <div className="eyebrow">ДОБРО ПОЖАЛОВАТЬ</div><h2>Войти в колонию</h2><p className="muted">Создайте новую игровую сессию или подключитесь к действующей.</p>
        <button className="button button-primary create-button" disabled={busy} onClick={onCreate}>＋ Создать новую сессию</button>
        <div className="form-divider"><span>или присоединиться</span></div>
        <label>ID игровой сессии<input value={sessionId} onChange={(event) => setSessionId(event.target.value)} placeholder="UUID сессии" /></label>
        <label>Ваше имя<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Например, Алексей" /></label>
        <label>Код приглашения<input value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} placeholder="Код от командира колонии" /></label>
        {error && <div className="alert">{error}</div>}
        <button className="button button-secondary create-button" disabled={busy || !sessionId.trim() || !displayName.trim() || !inviteCode.trim()}
          onClick={() => void onJoin(sessionId.trim(), displayName.trim(), inviteCode.trim())}>Подключиться к сессии</button>
        <div className="login-foot">Локальная игровая сеть · Ares-01 · До 20 участников</div>
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
        <div className="panel-heading"><div><div className="eyebrow">COLONY STATUS</div><h2>Состояние систем</h2></div><div className="panel-actions"><span className="live-tag"><i /> LIVE</span>{isCommander && <button className="button button-primary small-button" disabled={busy || (session?.current_period || 0) >= 4} onClick={() => void onAdvance()}>Завершить период →</button>}</div></div>
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

function Metric({ label, value, unit, detail }: { label: string; value: string; unit: string; detail: string }) {
  return <div className="metric-card"><span>{label}</span><div><strong>{value}</strong><small>{unit}</small></div><p>{detail}</p></div>;
}

function ColonyMap({ assets }: { assets: Array<Record<string, unknown>> }) {
  const [activeLayer, setActiveLayer] = useState("all");
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
    <div className="map-toolbar"><div><div className="eyebrow">WORLD MODEL / SURFACE PLAN</div><h2>Инфраструктура Ares-1</h2></div><span className="layer-chip">◉ Слои по типу объекта</span></div>
    <div className="map-layers" aria-label="Слои карты">
      <button className={activeLayer === "all" ? "map-layer active" : "map-layer"} onClick={() => setActiveLayer("all")}>Все объекты</button>
      {kinds.map((kind) => <button key={kind} className={activeLayer === kind ? "map-layer active" : "map-layer"} onClick={() => setActiveLayer(kind)}>{kind}</button>)}
    </div>
    <div className="map-canvas">
      <div className="map-grid" /><div className="map-horizon" />
      <div className="map-base"><span className="base-symbol">⌂</span><strong>ARES-1</strong><small>БАЗОВЫЙ МОДУЛЬ</small></div>
      {visible.map((asset, index) => {
        const properties = asset.properties as Record<string, unknown>;
        return <div className="map-marker" key={String(asset.id)} style={{ left: `${properties.x}%`, top: `${properties.y}%` }}><i />{String(asset.name || `Объект ${index + 1}`)}</div>;
      })}
      <div className="map-coordinate">SURFACE GRID · SCHEMATIC VIEW</div>
    </div>
    <div className="map-legend"><span><i className="legend-base" />Базовый модуль</span><span><i className="legend-asset" />Инфраструктурный объект</span><span className="muted">{visible.length} размещено · {unplaced.length} без координат</span></div>
    {unplaced.length > 0 && <div className="unplaced"><strong>Объекты выбранного слоя без координат</strong><p>Для этих объектов геометрия пока не задана. Они остаются в реестре и не получают условные координаты.</p>{unplaced.map((asset) => <span key={String(asset.id)}>{String(asset.name)} · {String(asset.kind || "other")}</span>)}</div>}
    {assets.length === 0 && <div className="empty-map-note">Инфраструктурные объекты появятся после завершения соответствующих проектов.</div>}
  </div>;
}

function ProjectList({ projects, auth, player, infrastructure, onRefresh }: {
  projects: Project[]; auth: Auth; player: Player | undefined;
  infrastructure: Array<Record<string, unknown>>; onRefresh: () => Promise<void>;
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
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState("");
  const roleSet = new Set(player?.roles || []);
  const canPropose = ["colony_commander", "domain_lead", "engineer"].some((role) => roleSet.has(role));
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
    oxygen: "Кислород, кг", food_solids: "Сухие пищевые вещества, кг",
    usable_water: "Подготовленная вода, кг", raw_ice: "Сырой лёд, кг"
  };
  const changeDomain = (code: string, checked: boolean) => setDomains((current) =>
    checked ? [...current, code] : current.filter((item) => item !== code)
  );
  const mutate = async (project: Project, action: string, extra: Record<string, unknown> = {}) => {
    setSaving(true); setActionError("");
    try {
      await request(`${API}/sessions/${auth.sessionId}/projects/${project.id}/${action}`, auth.token, {
        method: "POST", body: JSON.stringify({ expected_version: project.version, ...extra })
      });
      await onRefresh();
    } catch (reason) { setActionError((reason as Error).message); }
    finally { setSaving(false); }
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
    if (assetName.trim() && assetKind.trim()) {
      expected.infrastructure = [{ name: assetName.trim(), kind: assetKind.trim(), properties: { condition: 1 } }];
    }
    setSaving(true); setActionError("");
    try {
      await request(`${API}/sessions/${auth.sessionId}/projects`, auth.token, {
        method: "POST",
        body: JSON.stringify({
          title, description, project_type: projectType, domain_codes: domains,
          budget_credits: Number(budget), duration_periods: Number(duration),
          labor_fte: Number(laborFte), energy_kwh_per_period: Number(energy),
          resource_requirements: requirements, expected_result: expected
        })
      });
      setTitle(""); setDescription(""); setBudget("0"); setDuration("1");
      setLaborFte("0"); setEnergy("0"); setResourceInputs({}); setResourceOutputs({});
      setGenerationDelta("0"); setLoadDelta("0"); setAssetName(""); setAssetKind("infrastructure");
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
  const invalidAsset = Boolean(assetName.trim()) && !assetKind.trim();
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
        <label>Бюджет, кредиты<input type="number" min="0" max="1000000000" step="0.1" value={budget} onChange={(event) => setBudget(event.target.value)} /></label>
        <label>Длительность, периоды<input type="number" min="1" max="100" step="1" value={duration} onChange={(event) => setDuration(event.target.value)} /></label>
        <label>Трудозатраты, FTE<input type="number" min="0" max="1000000" step="0.1" value={laborFte} onChange={(event) => setLaborFte(event.target.value)} /></label>
        <label>Энергия за период, кВт·ч<input type="number" min="0" max="1000000" step="0.1" value={energy} onChange={(event) => setEnergy(event.target.value)} /></label>
      </div>
      <div className="form-section"><strong>Направления проекта</strong><div className="check-grid">{Object.entries(domainLabels).map(([code, label]) => <label className="check-option" key={code}><input type="checkbox" checked={domains.includes(code)} onChange={(event) => changeDomain(code, event.target.checked)} />{label}</label>)}</div></div>
      <div className="form-section"><strong>Материальные требования на старте проекта</strong><div className="form-grid">{Object.entries(resourceLabels).map(([code, label]) => <label key={code}>{label}<input type="number" min="0" step="0.01" value={resourceInputs[code] || ""} onChange={(event) => setResourceInputs((current) => ({ ...current, [code]: event.target.value }))} placeholder="0" /></label>)}</div></div>
      <div className="form-section"><strong>Ожидаемый дополнительный выпуск за период</strong><div className="form-grid">{Object.entries(resourceLabels).map(([code, label]) => <label key={code}>{label}<input type="number" min="0" step="0.01" value={resourceOutputs[code] || ""} onChange={(event) => setResourceOutputs((current) => ({ ...current, [code]: event.target.value }))} placeholder="0" /></label>)}</div></div>
      <div className="form-grid">
        <label>Дополнительная генерация, кВт<input type="number" min="0" step="0.1" value={generationDelta} onChange={(event) => setGenerationDelta(event.target.value)} /></label>
        <label>Дополнительная нагрузка, кВт<input type="number" min="0" step="0.1" value={loadDelta} onChange={(event) => setLoadDelta(event.target.value)} /></label>
        <label>Вводимый объект инфраструктуры<input value={assetName} onChange={(event) => setAssetName(event.target.value)} maxLength={120} placeholder="Например, резервный резервуар" /></label>
        <label>Тип объекта<input value={assetKind} onChange={(event) => setAssetKind(event.target.value)} maxLength={80} placeholder="Например, water_tank" /></label>
      </div>
      <label className="form-wide">Описание<textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={5000} rows={3} placeholder="Опишите цель, ограничения и ожидаемый результат проекта." /></label>
      <div className="form-foot"><span className="muted">Материалы списываются при запуске. Труд и энергия резервируются на период исполнения. Эффекты вводятся после подтверждения завершения.</span><button className="button button-primary" disabled={saving || title.trim().length < 3 || !description.trim() || domains.length === 0 || !validNumber(budget, 1000000000) || !validNumber(duration, 100) || !Number.isInteger(Number(duration)) || Number(duration) < 1 || !validNumber(laborFte) || !validNumber(energy) || !validNumber(generationDelta) || !validNumber(loadDelta) || invalidAsset || invalidResourceValues} onClick={() => void createProject()}>Создать черновик</button></div>
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
        </div>
        <div className="progress-track"><i style={{ width: `${Math.min(100, project.progress_periods / Math.max(1, project.duration_periods) * 100)}%` }} /></div>
        <div className="progress-caption"><span>Ход исполнения</span><b>{project.progress_periods} / {project.duration_periods} периодов</b></div>
        <div className="project-actions">
          {project.status === "draft" && (project.author_player_id === auth.playerId || roleSet.has("colony_commander")) && <button disabled={saving} onClick={() => void mutate(project, "submit")}>Отправить на согласование</button>}
          {project.status === "submitted" && ["colony_commander","domain_lead","finance_lead"].some((role) => roleSet.has(role)) && <>
            <button disabled={saving} onClick={() => void mutate(project, "approvals", { decision: "approve" })}>Согласовать</button>
            <button className="action-muted" disabled={saving} onClick={() => void mutate(project, "approvals", { decision: "reject" })}>Отклонить</button>
          </>}
          {project.status === "approved" && ["colony_commander","domain_lead","engineer"].some((role) => roleSet.has(role)) && <button disabled={saving} onClick={() => void mutate(project, "execute")}>Запустить проект</button>}
          {project.status === "executing" && project.progress_periods >= project.duration_periods && ["colony_commander","domain_lead","engineer"].some((role) => roleSet.has(role)) && <button disabled={saving} onClick={() => void mutate(project, "complete")}>Подтвердить завершение</button>}
          {["draft","submitted","approved"].includes(project.status) && (project.author_player_id === auth.playerId || roleSet.has("colony_commander")) && <button className="action-muted" disabled={saving} onClick={() => void mutate(project, "cancel")}>Отменить</button>}
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
        <div className="form-foot"><span className="muted">Назначение сохраняется на сервере. В сессии должен оставаться хотя бы один командир.</span><button className="button button-primary" disabled={saving || selectedRoles.length === 0} onClick={() => void saveRoles()}>Сохранить полномочия</button></div>
      </div>}
    </div>)}
  </section><section className="panel"><div className="eyebrow">RESEARCH REGISTER</div><h2>Технологии</h2>{technologies.length === 0 ? <p className="muted">Исследовательские результаты пока не зарегистрированы.</p> : technologies.map((technology) => <div className="technology-row" key={String(technology.id)}><strong>{String(technology.name)}</strong><span>Уровень {String(technology.level)}</span></div>)}</section></div>;
}
