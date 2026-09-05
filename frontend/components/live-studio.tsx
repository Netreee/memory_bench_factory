'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  ArrowRight,
  Boxes,
  BrainCircuit,
  CheckCircle2,
  CircleStop,
  Cpu,
  Database,
  FileText,
  Flame,
  FolderOpen,
  Gauge,
  History,
  LoaderCircle,
  Network,
  OctagonAlert,
  Pause,
  Play,
  Radio,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Timer,
  Upload,
  X,
  Zap,
} from 'lucide-react';

import { Button } from '@/components/ui/button';

const API_URL = process.env.NEXT_PUBLIC_MEMORY_FORGE_API ?? 'http://127.0.0.1:8791';
const MAX_FILE_BYTES = 200 * 1024;

const PIPELINE = [
  ['00', 'INPUT', '接收场景与样例'],
  ['01', 'WHITEPAPER', '多 Agent 议会建模'],
  ['02', 'WORLD', '生成可演化世界'],
  ['03', 'ORDERS', '构造能力订单'],
  ['03A', 'WELL-POSED', '良定义机械闸'],
  ['04', 'QUESTIONS', '题面生成'],
  ['05', 'CORPUS', '记忆语料渲染'],
  ['06', 'GROUNDING', '证据接地出厂'],
] as const;

const DEFAULT_SCENARIO =
  '构造一个研发团队长期推进复杂产品的记忆世界：成员、模块、里程碑、技术决策和风险会随迭代变化；会议纪要、周报与故障复盘从不同视角记录事实。';

type Health = { ok: boolean; live_ready?: boolean; engine?: string; model?: string };

type StageName = (typeof PIPELINE)[number][1];
type StageKey = 'input' | 'whitepaper' | 'world' | 'orders' | 'well_posed' | 'questions' | 'corpus' | 'grounding';
type StageStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled';

type RunSnapshot = {
  run_id: string;
  source: 'live' | 'recorded' | 'replay';
  status: 'starting' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  current_stage: StageKey | '';
  current_index: number;
  created?: string;
  elapsed_s: number;
  stall_s: number | null;
  llm_calls: number;
  llm_errors: number;
  step_now: string;
  output: string;
  stages: Array<{ name: StageKey; index: number; status: StageStatus; elapsed_s: number | null; artifact: string; ready: boolean }>;
  metrics: {
    entities: number;
    sessions: number;
    orders: number;
    well_posed: { n: number; kept: number; rate: number | null };
    questions: number;
    docs: number;
    chars: number;
    grounding: { n: number; grounded: number; survival: number | null };
  };
  agents: Array<{ id: string; name: string; role: string; status: 'waiting' | 'active' | 'complete' }>;
  recent_calls: Array<{ i: number; ts: number; latency_ms: number; ok: boolean; step: string; label: string }>;
  views: {
    input: { description: string; sample_name: string; sample_chars: number };
    whitepaper: { entity_noun: string; doc_genres: string[]; active_lines: string[] };
    world: { entity_names: string[]; n_sessions: number };
    questions: Array<{ question: string; line?: string; capability?: string; source: string }>;
    corpus_sessions: Array<{ id: string; date: string; docs: number; types: string[] }>;
    grounding: { overall: { n?: number; grounded?: number; survival?: number }; questions: Array<{ question: string; line: string; capability: string }> };
  };
  error: { type: string; message: string } | null;
};

type ReplayRun = {
  run_id: string;
  scenario: string;
  status: string;
  created?: string;
  completed_stages: number;
  llm_calls: number;
  has_06: boolean;
};

type ReplayEvent = {
  seq: number;
  at_ms: number;
  type: 'stage_started' | 'stage_completed' | 'call';
  stage: StageKey | '';
  i?: number;
  step?: string;
  label?: string;
  ok?: boolean;
  latency_ms?: number;
  question?: string;
};

type ReplayBundle = {
  run_id: string;
  duration_ms: number;
  original_duration_s: number;
  events: ReplayEvent[];
  final_snapshot: RunSnapshot;
};

const STAGE_LABELS: Record<StageKey, { index: string; name: StageName; cn: string }> = {
  input: { index: '00', name: 'INPUT', cn: '输入接收' },
  whitepaper: { index: '01', name: 'WHITEPAPER', cn: '议会白皮书' },
  world: { index: '02', name: 'WORLD', cn: '世界构建' },
  orders: { index: '03', name: 'ORDERS', cn: '能力订单' },
  well_posed: { index: '03A', name: 'WELL-POSED', cn: '良定义闸' },
  questions: { index: '04', name: 'QUESTIONS', cn: '题面生成' },
  corpus: { index: '05', name: 'CORPUS', cn: '语料渲染' },
  grounding: { index: '06', name: 'GROUNDING', cn: '接地出厂' },
};

export function LiveStudio({ onReplay }: { onReplay: () => void }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [description, setDescription] = useState(DEFAULT_SCENARIO);
  const [fileName, setFileName] = useState('');
  const [fileText, setFileText] = useState('');
  const [fileError, setFileError] = useState('');
  const [health, setHealth] = useState<Health | null>(null);
  const [screen, setScreen] = useState<'compose' | 'starting' | 'run'>('compose');
  const [runId, setRunId] = useState('');
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const [submitError, setSubmitError] = useState('');
  const [selectedStage, setSelectedStage] = useState<StageKey | null>(null);
  const [composeMode, setComposeMode] = useState<'live' | 'replay'>('live');
  const [replayRuns, setReplayRuns] = useState<ReplayRun[]>([]);
  const [selectedReplayId, setSelectedReplayId] = useState('');
  const [replayBundle, setReplayBundle] = useState<ReplayBundle | null>(null);
  const [replayCursor, setReplayCursor] = useState(0);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [replayClockKey, setReplayClockKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/api/health`, { signal: controller.signal })
      .then((response) => response.json())
      .then((payload) => setHealth(payload as Health))
      .catch(() => setHealth({ ok: false }));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const savedRun = window.localStorage.getItem('memory-forge-live-run');
    if (!savedRun) return;
    fetch(`${API_URL}/api/runs/${encodeURIComponent(savedRun)}`)
      .then((response) => {
        if (!response.ok) throw new Error('missing');
        return response.json();
      })
      .then((value) => {
        const payload = value as RunSnapshot;
        setRunId(payload.run_id);
        setSnapshot(payload);
        setScreen('run');
      })
      .catch(() => window.localStorage.removeItem('memory-forge-live-run'));
  }, []);

  useEffect(() => {
    if (composeMode !== 'replay') return;
    fetch(`${API_URL}/api/runs`)
      .then((response) => response.json())
      .then((value) => {
        const runs = (value as { runs?: ReplayRun[] }).runs ?? [];
        setReplayRuns(runs);
        setSelectedReplayId((current) => current || runs.find((run) => run.has_06)?.run_id || runs[0]?.run_id || '');
      })
      .catch(() => setSubmitError('无法读取本地历史 Run 列表'));
  }, [composeMode]);

  useEffect(() => {
    if (!runId || snapshot?.source === 'recorded' || snapshot?.source === 'replay' || ['succeeded', 'failed', 'cancelled'].includes(snapshot?.status ?? '')) return;
    let disposed = false;
    const poll = async () => {
      try {
        const response = await fetch(`${API_URL}/api/runs/${encodeURIComponent(runId)}`, { cache: 'no-store' });
        if (!response.ok) return;
        const payload = await response.json() as RunSnapshot;
        if (!disposed) {
          setSnapshot(payload);
          setScreen('run');
        }
      } catch {
        if (!disposed) setSubmitError('与本地引擎的连接暂时中断，正在继续重试。');
      }
    };
    void poll();
    const timer = window.setInterval(poll, 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [runId, snapshot?.source, snapshot?.status]);

  useEffect(() => {
    if (!replayBundle || !replayPlaying) return;
    const startedAt = performance.now() - replayCursor;
    const timer = window.setInterval(() => {
      const next = Math.min(replayBundle.duration_ms, performance.now() - startedAt);
      setReplayCursor(next);
      if (next >= replayBundle.duration_ms) setReplayPlaying(false);
    }, 80);
    return () => window.clearInterval(timer);
  }, [replayBundle, replayClockKey, replayPlaying]);

  useEffect(() => {
    if (!replayBundle) return;
    setSnapshot(buildReplaySnapshot(replayBundle, replayCursor));
  }, [replayBundle, replayCursor]);

  const preview = useMemo(
    () => fileText.split(/\r?\n/).filter(Boolean).slice(0, 3).join('\n'),
    [fileText],
  );

  const readFile = async (file?: File) => {
    setFileError('');
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.txt')) {
      setFileError('演示版只接收 .txt 文件');
      return;
    }
    if (file.size === 0 || file.size > MAX_FILE_BYTES) {
      setFileError(file.size === 0 ? '文件不能为空' : '文件需小于 200 KB');
      return;
    }
    const text = await file.text();
    if (!text.trim()) {
      setFileError('文件没有可用文本');
      return;
    }
    setFileName(file.name);
    setFileText(text);
  };

  const startRun = async () => {
    if (!description.trim() || !fileText || !health?.ok) return;
    setSubmitError('');
    setScreen('starting');
    setSnapshot(null);
    setReplayBundle(null);
    setReplayPlaying(false);
    try {
      const response = await fetch(`${API_URL}/api/runs`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ scenario_text: description, sample_name: fileName, sample_text: fileText }),
      });
      const payload = await response.json() as { run_id?: string; detail?: string | { message?: string; run_id?: string } };
      if (!response.ok) {
        const detail = payload?.detail;
        const message = typeof detail === 'string'
          ? detail
          : `${detail?.message ?? '本地任务未能启动'}${detail?.run_id ? ` · ${detail.run_id}` : ''}`;
        throw new Error(message);
      }
      if (!payload.run_id) throw new Error('本地引擎没有返回 Run ID');
      setRunId(payload.run_id);
      window.localStorage.setItem('memory-forge-live-run', payload.run_id);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : '无法连接本地引擎');
      setScreen('compose');
    }
  };

  const loadRecorded = async () => {
    setSubmitError('');
    setScreen('starting');
    try {
      const response = await fetch(`${API_URL}/api/recorded`);
      if (!response.ok) throw new Error('未找到可用的历史 Run');
      const payload = await response.json() as RunSnapshot;
      window.localStorage.removeItem('memory-forge-live-run');
      setSnapshot(payload);
      setRunId(payload.run_id);
      setScreen('run');
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : '历史 Run 载入失败');
      setScreen('compose');
    }
  };

  const loadReplay = async () => {
    if (!selectedReplayId) return;
    setSubmitError('');
    setScreen('starting');
    try {
      const response = await fetch(`${API_URL}/api/replay/${encodeURIComponent(selectedReplayId)}`);
      if (!response.ok) throw new Error('这个 Run 没有足够的回放记录');
      const bundle = await response.json() as ReplayBundle;
      window.localStorage.removeItem('memory-forge-live-run');
      setRunId(bundle.run_id);
      setReplayBundle(bundle);
      setReplayCursor(0);
      setReplayClockKey((value) => value + 1);
      setSnapshot(buildReplaySnapshot(bundle, 0));
      setReplayPlaying(true);
      setSelectedStage(null);
      setScreen('run');
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'Replay 载入失败');
      setScreen('compose');
    }
  };

  const cancelRun = async () => {
    if (!runId) return;
    setSubmitError('正在停止真实 worker…');
    try {
      const response = await fetch(`${API_URL}/api/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
      if (!response.ok) throw new Error('停止请求失败');
      const next = await fetch(`${API_URL}/api/runs/${encodeURIComponent(runId)}`).then((item) => item.json()) as RunSnapshot;
      setSnapshot(next);
      setSubmitError('');
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : '停止请求失败');
    }
  };

  const newRun = () => {
    window.localStorage.removeItem('memory-forge-live-run');
    setRunId('');
    setSnapshot(null);
    setReplayBundle(null);
    setReplayCursor(0);
    setReplayPlaying(false);
    setSelectedStage(null);
    setSubmitError('');
    setScreen('compose');
  };

  if (screen !== 'compose') {
    return (
      <RunConsole
        snapshot={snapshot}
        starting={screen === 'starting'}
        selectedStage={selectedStage}
        message={submitError}
        onSelectStage={setSelectedStage}
        onCancel={() => void cancelRun()}
        onNewRun={newRun}
        onReplay={onReplay}
        onRecorded={() => void loadRecorded()}
        replay={replayBundle ? {
          cursor: replayCursor,
          duration: replayBundle.duration_ms,
          originalDuration: replayBundle.original_duration_s,
          playing: replayPlaying,
          onToggle: () => {
            if (replayCursor >= replayBundle.duration_ms) {
              setReplayCursor(0);
              setReplayClockKey((value) => value + 1);
            }
            setReplayPlaying((value) => !value || replayCursor >= replayBundle.duration_ms);
          },
          onRestart: () => { setReplayCursor(0); setReplayClockKey((value) => value + 1); setReplayPlaying(true); setSelectedStage(null); },
        } : null}
      />
    );
  }

  return (
    <main className="studio-app">
      <div className="forge-grid" aria-hidden="true" />
      <div className="forge-aurora" aria-hidden="true" />
      <div className="forge-grain" aria-hidden="true" />

      <header className="studio-header">
        <div className="forge-brand">
          <div className="forge-logo" aria-hidden="true"><span>MF</span></div>
          <div>
            <p className="forge-brand-name">MEMORY FORGE</p>
            <p className="forge-brand-subtitle">LIVE BENCHMARK FOUNDRY</p>
          </div>
        </div>
        <div className="studio-signal">
          <Radio size={13} />
          <span>{health === null ? 'PROBING LOCAL ENGINE' : health.ok ? health.live_ready ? 'LIVE + REPLAY ONLINE' : 'REPLAY ENGINE ONLINE' : 'LOCAL ENGINE OFFLINE'}</span>
          <i className={health?.ok ? 'is-online' : ''} />
        </div>
        <button type="button" className="studio-history" onClick={onReplay}>
          <History size={14} /> 电影回放
        </button>
      </header>

      <section className="studio-hero">
        <div className="studio-copy">
          <p className="studio-kicker"><span>{composeMode === 'live' ? 'LIVE / 06' : 'REPLAY / 60S'}</span>{composeMode === 'live' ? ' REAL PIPELINE · LOCAL ONLY' : ' EXISTING RUN · TIME COMPRESSED'}</p>
          <h1>{composeMode === 'live' ? <>给它一个世界。<br /><em>看 Benchmark 被铸造。</em></> : <>选一段历史。<br /><em>让铸造过程再次发生。</em></>}</h1>
          <p className="studio-lede">{composeMode === 'live' ? '输入场景，交给一个 TXT 样例。接下来每一次发光、每一个数字，都由本地真实流水线触发。' : 'Replay 不启动模型，也不需要上传日志。选择一个已有 Run，系统会用它的真实阶段和调用时间重建 60 秒回放。'}</p>
          <div className="studio-contract">
            <span><ShieldCheck size={14} /> {composeMode === 'live' ? 'TXT ONLY' : 'RUN ID ONLY'}</span>
            <span><Gauge size={14} /> {composeMode === 'live' ? 'STOP AT 06' : '60S COMPRESSED'}</span>
            <span><Activity size={14} /> REAL EVENTS</span>
          </div>
        </div>

        <div className="studio-compose-panel">
          <div className="compose-heading">
            <div><span>01</span><p>{composeMode === 'live' ? '定义铸造任务' : '选择历史 Run'}</p></div>
            <small>{composeMode === 'live' ? 'OPERATOR INPUT' : 'REPLAY INPUT'}</small>
          </div>
          <div className="studio-mode-switch" role="tablist" aria-label="运行模式">
            <button type="button" role="tab" aria-selected={composeMode === 'live'} className={composeMode === 'live' ? 'is-active' : ''} onClick={() => setComposeMode('live')}><Flame />真实运行</button>
            <button type="button" role="tab" aria-selected={composeMode === 'replay'} className={composeMode === 'replay' ? 'is-active' : ''} onClick={() => setComposeMode('replay')}><Play />重放历史</button>
          </div>

          {composeMode === 'live' ? <>
            <label className="studio-field">
              <span>SCENARIO / 场景描述</span>
              <textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={1200} />
              <small>{description.length} / 1200</small>
            </label>

            <button
              type="button"
              className={fileName ? 'studio-dropzone has-file' : 'studio-dropzone'}
              onClick={() => fileInput.current?.click()}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => { event.preventDefault(); void readFile(event.dataTransfer.files[0]); }}
            >
              <input ref={fileInput} type="file" accept=".txt,text/plain" hidden onChange={(event) => void readFile(event.target.files?.[0])} />
              {fileName ? <FileText size={21} /> : <Upload size={21} />}
              <strong>{fileName || '上传一个 TXT 示例文件'}</strong>
              <span>{fileName ? `${fileText.length.toLocaleString()} 字符 · 已锁定` : '拖到这里，或点击选择 · ≤ 200 KB'}</span>
              {preview && <pre>{preview}</pre>}
            </button>
            {fileError && <p className="studio-form-error">{fileError}</p>}

            <Button className="studio-ignite" size="lg" disabled={!description.trim() || !fileText || !health?.live_ready} onClick={() => void startRun()}>
              {health === null ? <LoaderCircle className="studio-spin" /> : <Flame />}
              {health === null ? '正在连接本地引擎' : health.live_ready ? '点火 · 启动真实铸造' : health.ok ? '未配置模型 · 仍可重放历史' : '本地引擎未启动'}
              <ArrowRight data-icon="inline-end" />
            </Button>
            <button type="button" className="studio-recorded-fallback" disabled={!health?.ok} onClick={() => void loadRecorded()}>
              <History size={12} /> 直接查看已完成 Run <span>RECORDED</span>
            </button>
          </> : <>
            <div className="replay-run-list" role="radiogroup" aria-label="选择历史 Run">
              {replayRuns.map((run) => (
                <button type="button" role="radio" aria-checked={selectedReplayId === run.run_id} className={selectedReplayId === run.run_id ? 'is-selected' : ''} key={run.run_id} onClick={() => setSelectedReplayId(run.run_id)}>
                  <i>{run.has_06 ? <CheckCircle2 /> : <OctagonAlert />}</i>
                  <div><strong>{run.run_id}</strong><span>{run.scenario} · {run.completed_stages}/8 STAGES · {run.llm_calls} CALLS</span></div>
                  <b>{run.has_06 ? '06 READY' : run.status.toUpperCase()}</b>
                </button>
              ))}
              {!replayRuns.length && <div className="replay-list-empty"><LoaderCircle />正在读取本地 Run…</div>}
            </div>
            <p className="replay-input-note"><FolderOpen />输入就是一个 run_id；完整 Run 目录已经包含回放所需记录。</p>
            <Button className="studio-ignite" size="lg" disabled={!selectedReplayId || !health?.ok} onClick={() => void loadReplay()}>
              <Play />载入并重放 60 秒<ArrowRight data-icon="inline-end" />
            </Button>
          </>}
          {submitError && <p className="studio-form-error">{submitError}</p>}
          {!health?.ok && health !== null && <p className="studio-engine-hint">运行 <code>./scripts/live-demo.sh</code> 启动本地引擎</p>}
        </div>
      </section>

      <section className="studio-pipeline" aria-label="真实流水线预检">
        <div className="pipeline-intro">
          <span>{composeMode === 'live' ? 'EXECUTION CONTRACT' : 'REPLAY SOURCE'}</span>
          <strong>00 → 06</strong>
          <p>{composeMode === 'live' ? '本次不会启动评测 Arena。' : '不启动 worker，不重复调用模型。'}</p>
        </div>
        <div className="pipeline-track">
          {PIPELINE.map(([index, name, detail], itemIndex) => (
            <div className="pipeline-node" key={name}>
              <i>{index}</i>
              <strong>{name}</strong>
              <span>{detail}</span>
              {itemIndex < PIPELINE.length - 1 && <b aria-hidden="true" />}
            </div>
          ))}
        </div>
        <div className="pipeline-output">
          <FolderOpen size={17} />
          <div><span>{composeMode === 'live' ? 'OUTPUT ARTIFACT' : 'REPLAY INPUT'}</span><strong>{composeMode === 'live' ? '06_grounded_questions.json' : 'output/runs/&lt;run_id&gt;'}</strong></div>
        </div>
      </section>
    </main>
  );
}

function RunConsole({
  snapshot,
  starting,
  selectedStage,
  message,
  onSelectStage,
  onCancel,
  onNewRun,
  onReplay,
  onRecorded,
  replay,
}: {
  snapshot: RunSnapshot | null;
  starting: boolean;
  selectedStage: StageKey | null;
  message: string;
  onSelectStage: (stage: StageKey | null) => void;
  onCancel: () => void;
  onNewRun: () => void;
  onReplay: () => void;
  onRecorded: () => void;
  replay: { cursor: number; duration: number; originalDuration: number; playing: boolean; onToggle: () => void; onRestart: () => void } | null;
}) {
  const status = snapshot?.status ?? 'starting';
  const isTerminal = ['succeeded', 'failed', 'cancelled'].includes(status);
  const isReplay = snapshot?.source === 'replay';
  const isLive = snapshot?.source === 'live' && !isTerminal;
  const completed = snapshot?.stages.filter((stage) => stage.status === 'succeeded').length ?? 0;
  const currentStage = snapshot?.current_stage || 'input';
  const displayStage = selectedStage ?? currentStage;
  const progress = replay ? Math.round((replay.cursor / replay.duration) * 100) : snapshot?.status === 'succeeded' ? 100 : Math.round((completed / PIPELINE.length) * 100);

  return (
    <main className="live-app" data-status={status}>
      <div className="forge-grid" aria-hidden="true" />
      <div className="forge-aurora" aria-hidden="true" />
      <div className="forge-grain" aria-hidden="true" />

      <header className="live-header">
        <div className="forge-brand">
          <div className="forge-logo" aria-hidden="true"><span>MF</span></div>
          <div>
            <p className="forge-brand-name">MEMORY FORGE</p>
            <p className="forge-brand-subtitle">LIVE BENCHMARK FOUNDRY</p>
          </div>
        </div>
        <div className={`live-mode ${isLive ? 'is-live' : ''}`}>
          <i />
          <span>{isReplay ? `REPLAY · ${replay?.playing ? 'PLAYING' : 'PAUSED'}` : snapshot?.source === 'recorded' ? 'RECORDED RUN' : isTerminal ? status.toUpperCase() : 'LOCAL LIVE'}</span>
          <b />
          <span>{snapshot?.step_now ?? '正在创建隔离 Run'}</span>
        </div>
        <div className="live-run-id">
          <span>{snapshot?.run_id ?? 'ALLOCATING RUN ID…'}</span>
          <small>{isReplay ? '60S COMPRESSED' : snapshot?.source === 'recorded' ? 'HISTORICAL DATA' : 'PINNED RUN'}</small>
        </div>
      </header>

      <div className="live-layout">
        <nav className="live-stage-rail" aria-label="真实 Run 阶段">
          <div className="live-rail-title"><span>PIPELINE</span><b>{progress}%</b></div>
          {(snapshot?.stages ?? STAGE_ORDER_FALLBACK).map((stage) => {
            const label = STAGE_LABELS[stage.name];
            const active = displayStage === stage.name;
            const inspectable = stage.ready || stage.status === 'running';
            return (
              <button
                type="button"
                key={stage.name}
                disabled={!inspectable}
                className={`live-stage-node is-${stage.status} ${active ? 'is-viewing' : ''}`}
                onClick={() => onSelectStage(stage.name === currentStage ? null : stage.name)}
              >
                <i><span>{label.index}</span></i>
                <div><strong>{label.name}</strong><small>{label.cn}</small></div>
                <em>{stage.status === 'succeeded' ? <CheckCircle2 /> : stage.status === 'running' ? <LoaderCircle /> : stage.status === 'failed' ? <X /> : ''}</em>
              </button>
            );
          })}
          <div className="live-rail-note">
            <ShieldCheck size={13} />
            <span>{isReplay ? '阶段来自历史 manifest' : '真实状态来自 manifest'}<br />{isReplay ? '本地时钟只移动回放游标' : '不使用计时器伪造进度'}</span>
          </div>
        </nav>

        <section className="live-main-stage">
          <div className="live-scene-heading">
            <div>
              <p>{snapshot?.status === 'succeeded' && !selectedStage ? 'DELIVERY / 出厂' : `${STAGE_LABELS[displayStage].index} / ${STAGE_LABELS[displayStage].name}`}</p>
              <h1>{snapshot?.status === 'succeeded' && !selectedStage ? '题库已通过最后一道闸' : STAGE_LABELS[displayStage].cn}</h1>
            </div>
            <div className="live-scene-state">
              {selectedStage && selectedStage !== currentStage && <button type="button" onClick={() => onSelectStage(null)}>回到现场</button>}
              <span>{snapshot?.status === 'succeeded' && !selectedStage ? 'ARTIFACT READY' : snapshot?.stages.find((stage) => stage.name === displayStage)?.status.toUpperCase() ?? 'STARTING'}</span>
            </div>
          </div>

          <div className="live-scene-canvas" key={`${displayStage}-${snapshot?.llm_calls ?? 0}-${snapshot?.metrics.docs ?? 0}`}>
            {!snapshot || starting ? <IgnitionView /> : snapshot.status === 'succeeded' && !selectedStage ? (
              <DeliveryView snapshot={snapshot} onReplay={onReplay} />
            ) : snapshot.status === 'failed' && !selectedStage ? (
              <FailureView snapshot={snapshot} onRecorded={onRecorded} />
            ) : snapshot.status === 'cancelled' && !selectedStage ? (
              <CancelledView />
            ) : (
              <RunStageView stage={displayStage} snapshot={snapshot} />
            )}
          </div>

          <div className="live-event-strip">
            <span><Activity size={12} /> {isReplay ? 'REPLAY EVENT STREAM' : 'REAL EVENT STREAM'}</span>
            <div>
              {(snapshot?.recent_calls ?? []).slice(-5).map((call) => (
                <i key={call.i} className={call.ok ? '' : 'is-error'}>
                  <b>#{String(call.i).padStart(3, '0')}</b>{call.label}<em>{(call.latency_ms / 1000).toFixed(1)}s</em>
                </i>
              ))}
              {!snapshot?.recent_calls.length && <i><b>SYS</b>等待第一个真实事件…</i>}
            </div>
          </div>
        </section>

        <aside className="live-telemetry">
          <div className="live-telemetry-heading"><span>RUN TELEMETRY</span><Radio size={13} /></div>
          <LiveMetric label="LLM CALLS" value={snapshot?.llm_calls ?? 0} note={`${snapshot?.llm_errors ?? 0} errors`} />
          <LiveMetric label={isReplay ? 'REPLAY TIME' : 'ELAPSED'} value={formatDuration(replay ? replay.cursor / 1000 : snapshot?.elapsed_s ?? 0)} note={isReplay ? `original ${formatDuration(replay?.originalDuration ?? 0)}` : snapshot?.stall_s ? `last event ${snapshot.stall_s}s ago` : 'wall clock'} />
          <LiveMetric label="ENTITIES / SESSIONS" value={`${snapshot?.metrics.entities ?? 0} / ${snapshot?.metrics.sessions ?? 0}`} note="world state" />
          <LiveMetric label="QUESTIONS" value={snapshot?.metrics.questions ?? snapshot?.views.questions.length ?? 0} note={`${snapshot?.metrics.orders ?? 0} orders`} />
          <LiveMetric label="CORPUS" value={snapshot?.metrics.docs ?? 0} note={`${(snapshot?.metrics.chars ?? 0).toLocaleString()} chars`} />
          <LiveMetric
            label="GROUNDED"
            value={snapshot?.metrics.grounding.grounded ?? 0}
            note={snapshot?.metrics.grounding.survival == null ? 'waiting for 06' : `${Math.round(snapshot.metrics.grounding.survival * 100)}% survival`}
            accent={snapshot?.status === 'succeeded'}
          />
          <div className="live-runtime-note">
            <Timer size={14} />
            <p>{isReplay ? <><strong>这是历史重放</strong>不创建 worker，不重复调用模型；60 秒后停在真实最终产物。</> : <><strong>长任务是正常的</strong>真实演示通常需要 20–40 分钟；卡片只在产物真正出现后亮起。</>}</p>
          </div>
          {message && <div className="live-message">{message}</div>}
        </aside>
      </div>

      <footer className="live-controls">
        <div className="live-progress"><span><i style={{ width: `${progress}%` }} /></span><b>{replay ? `${formatDuration(replay.cursor / 1000)} / 01:00` : `${completed} / 8 STAGES`}</b></div>
        <p><i className={isLive ? 'is-live' : ''} />{isReplay ? `TIME COMPRESSED · ${snapshot?.step_now ?? '准备重放'}` : snapshot?.source === 'recorded' ? '已载入历史 Run，当前不是实时任务' : snapshot?.step_now ?? '正在分配本地工作进程'}</p>
        <div>
          {replay && <Button variant="ghost" onClick={replay.onRestart}><RotateCcw />从头重放</Button>}
          {replay && <Button className="live-replay-button" onClick={replay.onToggle}>{replay.playing ? <Pause /> : <Play />}{replay.playing ? '暂停' : replay.cursor >= replay.duration ? '再次播放' : '继续'}</Button>}
          {isLive && <Button variant="ghost" onClick={onCancel}><CircleStop />停止任务</Button>}
          {isTerminal && <Button variant="ghost" onClick={onNewRun}><RotateCcw />新建任务</Button>}
          {snapshot?.status === 'succeeded' && !replay && <Button className="live-replay-button" onClick={onReplay}><Sparkles />进入电影回放</Button>}
        </div>
      </footer>
    </main>
  );
}

const STAGE_ORDER_FALLBACK: RunSnapshot['stages'] = (Object.keys(STAGE_LABELS) as StageKey[]).map((name, index) => ({
  name,
  index,
  status: index === 0 ? 'running' : 'pending',
  elapsed_s: null,
  artifact: '',
  ready: index === 0,
}));

function buildReplaySnapshot(bundle: ReplayBundle, cursor: number): RunSnapshot {
  const final = bundle.final_snapshot;
  const visible = bundle.events.filter((event) => event.at_ms <= cursor);
  const completed = new Set(visible.filter((event) => event.type === 'stage_completed').map((event) => event.stage));
  const started = visible.filter((event) => event.type === 'stage_started');
  const currentStage = (started.at(-1)?.stage || 'input') as StageKey;
  const calls = visible.filter((event) => event.type === 'call');
  const phraseCalls = calls.filter((event) => event.step === 'phrase').length;
  const atEnd = cursor >= bundle.duration_ms;
  const stageDone = (stage: StageKey) => completed.has(stage);
  const replayQuestions = calls
    .filter((event) => event.step === 'phrase' && event.question)
    .slice(-12)
    .map((event) => ({ question: event.question || '', line: 'LIVE', capability: 'PHRASE', source: 'replay' }));
  const visibleQuestions = stageDone('questions') ? final.views.questions : replayQuestions;

  return {
    ...final,
    source: 'replay',
    status: atEnd ? final.status : 'running',
    current_stage: atEnd ? '' : currentStage,
    current_index: atEnd ? -1 : final.stages.findIndex((stage) => stage.name === currentStage),
    elapsed_s: Math.round((cursor / bundle.duration_ms) * bundle.original_duration_s),
    stall_s: null,
    llm_calls: calls.length,
    llm_errors: calls.filter((event) => event.ok === false).length,
    step_now: calls.at(-1)?.label || `${STAGE_LABELS[currentStage].cn} · 历史重放`,
    stages: final.stages.map((stage) => ({
      ...stage,
      status: stageDone(stage.name) ? 'succeeded' : stage.name === currentStage && !atEnd ? 'running' : 'pending',
      ready: stageDone(stage.name),
    })),
    metrics: {
      entities: stageDone('world') ? final.metrics.entities : 0,
      sessions: stageDone('world') ? final.metrics.sessions : 0,
      orders: stageDone('orders') ? final.metrics.orders : 0,
      well_posed: stageDone('well_posed') ? final.metrics.well_posed : { n: 0, kept: 0, rate: null },
      questions: stageDone('questions') ? final.metrics.questions : phraseCalls,
      docs: stageDone('corpus') ? final.metrics.docs : 0,
      chars: stageDone('corpus') ? final.metrics.chars : 0,
      grounding: stageDone('grounding') ? final.metrics.grounding : { n: 0, grounded: 0, survival: null },
    },
    agents: final.agents.map((agent) => ({
      ...agent,
      status: calls.some((event) => event.step === agent.id) ? 'complete' : currentStage === 'whitepaper' ? 'active' : 'waiting',
    })),
    recent_calls: calls.slice(-8).map((event, index) => ({
      i: event.i || index + 1,
      ts: event.at_ms / 1000,
      latency_ms: event.latency_ms || 0,
      ok: event.ok !== false,
      step: event.step || '',
      label: event.label || '流水线调用',
    })),
    views: {
      input: final.views.input,
      whitepaper: stageDone('whitepaper') ? final.views.whitepaper : { entity_noun: '', doc_genres: [], active_lines: [] },
      world: stageDone('world') ? final.views.world : { entity_names: [], n_sessions: 0 },
      questions: visibleQuestions,
      corpus_sessions: stageDone('corpus') ? final.views.corpus_sessions : [],
      grounding: stageDone('grounding') ? final.views.grounding : { overall: {}, questions: [] },
    },
  };
}

function RunStageView({ stage, snapshot }: { stage: StageKey; snapshot: RunSnapshot }) {
  if (stage === 'input') return <IntakeView snapshot={snapshot} />;
  if (stage === 'whitepaper') return <CouncilView snapshot={snapshot} />;
  if (stage === 'world') return <WorldBuildView snapshot={snapshot} />;
  if (stage === 'orders' || stage === 'well_posed' || stage === 'questions') return <QuestionBuildView snapshot={snapshot} stage={stage} />;
  if (stage === 'corpus') return <CorpusBuildView snapshot={snapshot} />;
  return <GroundingBuildView snapshot={snapshot} />;
}

function IgnitionView() {
  return (
    <div className="ignition-view">
      <div className="ignition-core"><Flame /><i /><i /><i /></div>
      <p>CREATING ISOLATED RUN</p>
      <h2>正在点燃本地铸造炉</h2>
      <span>Run ID 返回后，所有视觉节点将由真实文件事件接管。</span>
    </div>
  );
}

function IntakeView({ snapshot }: { snapshot: RunSnapshot }) {
  const input = snapshot.views.input;
  return (
    <div className="intake-view">
      <div className="intake-beam"><Upload /><span /><FileText /></div>
      <div className="intake-manifest">
        <p><CheckCircle2 /> INPUT SEALED</p>
        <h2>{input.sample_name || '正在写入 00_input.json'}</h2>
        <span>{input.sample_chars.toLocaleString()} CHARACTERS · UTF-8 · TXT</span>
        <blockquote>{input.description || '场景描述正在进入隔离 Run…'}</blockquote>
      </div>
      <ArtifactChip name="00_input.json" ready={snapshot.stages[0]?.ready} />
    </div>
  );
}

function CouncilView({ snapshot }: { snapshot: RunSnapshot }) {
  const view = snapshot.views.whitepaper;
  return (
    <div className="council-live-view">
      <div className="council-live-orbit">
        <div className="council-live-core"><BrainCircuit /><span>COUNCIL</span><b>{snapshot.agents.filter((agent) => agent.status === 'complete').length}/7</b></div>
        {snapshot.agents.map((agent, index) => (
          <div key={agent.id} className={`council-live-agent is-${agent.status}`} style={{ '--agent-index': index } as React.CSSProperties}>
            <i>{String(index + 1).padStart(2, '0')}</i><strong>{agent.name}</strong><span>{agent.role}</span>
          </div>
        ))}
      </div>
      <div className="council-blueprint">
        <p><Sparkles /> WHITEPAPER SIGNAL</p>
        <h3>{view.entity_noun ? `已识别核心实体：${view.entity_noun}` : '多 Agent 正在汇聚世界蓝图'}</h3>
        <div>{view.active_lines.map((line) => <span key={line}>{line}</span>)}</div>
        <small>{view.doc_genres.length ? view.doc_genres.join(' / ') : snapshot.step_now}</small>
        <ArtifactChip name="01_whitepaper.json" ready={snapshot.stages[1]?.ready} />
      </div>
    </div>
  );
}

function WorldBuildView({ snapshot }: { snapshot: RunSnapshot }) {
  const nodes = snapshot.views.world.entity_names;
  return (
    <div className="world-live-view">
      <div className="world-grid-sphere"><i /><i /><i /></div>
      <div className="world-node-field">
        {(nodes.length ? nodes : ['ENTITY SEED', 'RELATION', 'EVENT', 'TIMELINE']).map((name, index) => (
          <div key={`${name}-${index}`} className={nodes.length ? 'world-live-node is-real' : 'world-live-node'} style={{ '--node-index': index } as React.CSSProperties}>
            <Network /><strong>{name}</strong><span>{nodes.length ? 'ENTITY LOCKED' : 'AWAITING WORLD'}</span>
          </div>
        ))}
      </div>
      <div className="world-counter"><span>WORLD STATE</span><strong>{snapshot.metrics.entities}</strong><small>ENTITIES</small><b>{snapshot.metrics.sessions} SESSIONS</b></div>
      <ArtifactChip name="02_world.json" ready={snapshot.stages[2]?.ready} />
    </div>
  );
}

function QuestionBuildView({ snapshot, stage }: { snapshot: RunSnapshot; stage: StageKey }) {
  const gate = snapshot.metrics.well_posed;
  return (
    <div className="question-live-view">
      <div className="question-flow">
        <div><Boxes /><strong>{snapshot.metrics.orders}</strong><span>ORDERS</span></div>
        <i><Zap /></i>
        <div className={stage === 'well_posed' ? 'is-active' : ''}><ShieldCheck /><strong>{gate.kept}</strong><span>WELL-POSED</span></div>
        <i><Zap /></i>
        <div className={stage === 'questions' ? 'is-active' : ''}><BrainCircuit /><strong>{snapshot.metrics.questions || snapshot.views.questions.length}</strong><span>QUESTIONS</span></div>
      </div>
      <div className="live-question-stack">
        {snapshot.views.questions.length ? snapshot.views.questions.slice(-4).map((question, index) => (
          <article key={`${question.question}-${index}`} style={{ '--card-index': index } as React.CSSProperties}>
            <div><span>{question.line || 'LIVE'}</span><b>{question.capability || 'PHRASE'}</b></div>
            <p>{question.question}</p>
          </article>
        )) : <div className="awaiting-cards"><LoaderCircle /><p>{stage === 'questions' ? '等待第一道真实题面落盘' : '机械订单正在穿过能力模具'}</p></div>}
      </div>
      <ArtifactChip name={stage === 'questions' ? '04_questions.json' : stage === 'well_posed' ? '03_well_posed_report.json' : '03_orders.json'} ready={snapshot.stages.find((item) => item.name === stage)?.ready} />
    </div>
  );
}

function CorpusBuildView({ snapshot }: { snapshot: RunSnapshot }) {
  const sessions = snapshot.views.corpus_sessions;
  return (
    <div className="corpus-live-view">
      <div className="corpus-vault">
        <Database />
        <span>MEMORY CORPUS</span>
        <strong>{snapshot.metrics.docs}</strong>
        <small>REAL DOCUMENTS</small>
        <i />
      </div>
      <div className="corpus-session-stream">
        {sessions.length ? sessions.slice(-7).map((session, index) => (
          <article key={`${session.id}-${index}`}>
            <b>{session.id || `SESSION ${index + 1}`}</b><span>{session.date}</span><strong>{session.docs} DOCS</strong><small>{session.types.join(' · ')}</small>
          </article>
        )) : <div className="awaiting-cards"><LoaderCircle /><p>第一批文档正在被真实渲染</p></div>}
      </div>
      <ArtifactChip name="05_corpus.json" ready={snapshot.stages[6]?.ready} />
    </div>
  );
}

function GroundingBuildView({ snapshot }: { snapshot: RunSnapshot }) {
  const result = snapshot.metrics.grounding;
  return (
    <div className="grounding-live-view">
      <div className="grounding-reactor">
        <ShieldCheck />
        <i /><i /><i />
        <span>MECHANICAL GATE</span>
        <strong>{result.survival == null ? '—' : `${Math.round(result.survival * 100)}%`}</strong>
      </div>
      <div className="grounding-ledger">
        <div><span>CANDIDATES</span><strong>{result.n || snapshot.metrics.questions}</strong></div>
        <div><span>GROUNDED</span><strong>{result.grounded}</strong></div>
        <div><span>REJECTED</span><strong>{Math.max(0, result.n - result.grounded)}</strong></div>
        <p>{snapshot.stages[7]?.ready ? '证据逐字匹配完成，出厂题库已经封存。' : '纯代码接地闸执行中；不会调用额外 LLM。'}</p>
      </div>
      <ArtifactChip name="06_grounded_questions.json" ready={snapshot.stages[7]?.ready} />
    </div>
  );
}

function DeliveryView({ snapshot, onReplay }: { snapshot: RunSnapshot; onReplay: () => void }) {
  const grounding = snapshot.metrics.grounding;
  return (
    <div className="delivery-view">
      <div className="delivery-halo"><CheckCircle2 /><i /><i /><i /></div>
      <p>RUN COMPLETE · STOPPED AT 06</p>
      <h2>{grounding.grounded}<span> 道接地题</span></h2>
      <div className="delivery-metrics">
        <span><b>{snapshot.metrics.entities}</b> 实体</span>
        <span><b>{snapshot.metrics.sessions}</b> Sessions</span>
        <span><b>{snapshot.metrics.docs}</b> 文档</span>
        <span><b>{grounding.survival == null ? '—' : `${Math.round(grounding.survival * 100)}%`}</b> 接地率</span>
      </div>
      <div className="delivery-artifact"><FolderOpen /><div><small>SEALED ARTIFACT</small><strong>{snapshot.output}</strong></div><ShieldCheck /></div>
      <Button className="delivery-replay" onClick={onReplay}><Sparkles />将本次成果切入电影化回放</Button>
    </div>
  );
}

function FailureView({ snapshot, onRecorded }: { snapshot: RunSnapshot; onRecorded: () => void }) {
  return (
    <div className="failure-view">
      <div className="failure-icon"><OctagonAlert /></div>
      <p>MECHANICAL HARD STOP</p>
      <h2>流水线拒绝了这次构建</h2>
      <span>{snapshot.error?.type || 'PIPELINE ERROR'} · {snapshot.error?.message || '当前代码路径未能产出有效的 06 文件。'}</span>
      <small>失败是本次真实运行的结果，没有被回放数据覆盖。</small>
      <Button variant="outline" onClick={onRecorded}><History />切换到已完成历史 Run</Button>
    </div>
  );
}

function CancelledView() {
  return (
    <div className="failure-view is-cancelled">
      <div className="failure-icon"><CircleStop /></div>
      <p>WORKER TERMINATED</p>
      <h2>真实任务已停止</h2>
      <span>本地 worker 进程组已经退出；旧计数不会继续增长。</span>
    </div>
  );
}

function ArtifactChip({ name, ready }: { name: string; ready?: boolean }) {
  return <div className={`artifact-chip ${ready ? 'is-ready' : ''}`}><FileText /><span>{name}</span><b>{ready ? 'READY' : 'AWAITING'}</b></div>;
}

function LiveMetric({ label, value, note, accent = false }: { label: string; value: string | number; note: string; accent?: boolean }) {
  return <div className={`live-metric ${accent ? 'is-accent' : ''}`}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>;
}

function formatDuration(seconds: number) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = Math.floor(seconds % 60);
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}` : `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}
