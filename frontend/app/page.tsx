'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronRight,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  X,
  Zap,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { LiveStudio } from '@/components/live-studio';
import {
  ARENA_SYSTEMS,
  CAPABILITY_CHANNELS,
  CAPABILITY_FINGERPRINT,
  COUNCIL_AGENTS,
  DEMO_META,
  GATE_CASES,
  SCENES,
  SPOTLIGHT_QUESTIONS,
  WORLD_SERIES,
} from '@/lib/demo-data';

const SCENE_DURATION_MS = 11_500;
const TICK_MS = 80;

export default function Home() {
  const [surface, setSurface] = useState<'studio' | 'replay'>(
    process.env.NEXT_PUBLIC_MEMORY_FORGE_API ? 'studio' : 'replay',
  );

  if (surface === 'studio') {
    return <LiveStudio onReplay={() => setSurface('replay')} />;
  }

  return <ReplayDeck onExit={() => setSurface('studio')} />;
}

function ReplayDeck({ onExit }: { onExit: () => void }) {
  const [scene, setScene] = useState(0);
  const [progress, setProgress] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [started, setStarted] = useState(false);
  const [sequenceKey, setSequenceKey] = useState(0);

  const current = SCENES[scene];

  const goToScene = useCallback((next: number, keepPlaying = false) => {
    const bounded = Math.max(0, Math.min(SCENES.length - 1, next));
    setScene(bounded);
    setProgress(0);
    setStarted(true);
    setPlaying(keepPlaying);
    setSequenceKey((key) => key + 1);
  }, []);

  const reset = useCallback(() => {
    setScene(0);
    setProgress(0);
    setPlaying(false);
    setStarted(false);
    setSequenceKey((key) => key + 1);
  }, []);

  const togglePlayback = useCallback(() => {
    if (!started || (scene === SCENES.length - 1 && progress >= 100)) {
      setScene(0);
      setProgress(0);
      setStarted(true);
      setPlaying(true);
      setSequenceKey((key) => key + 1);
      return;
    }
    setPlaying((value) => !value);
  }, [progress, scene, started]);

  const toggleFullscreen = useCallback(async () => {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await document.documentElement.requestFullscreen();
    }
  }, []);

  useEffect(() => {
    const requestedScene = Number(new URLSearchParams(window.location.search).get('scene'));
    const timer = window.setTimeout(() => {
      if (Number.isInteger(requestedScene) && requestedScene >= 1 && requestedScene <= SCENES.length) {
        setScene(requestedScene - 1);
        setStarted(true);
        setSequenceKey((key) => key + 1);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!playing) return;

    const interval = window.setInterval(() => {
      setProgress((value) => {
        const next = value + (TICK_MS / SCENE_DURATION_MS) * 100;
        if (next < 100) return next;

        if (scene < SCENES.length - 1) {
          setScene((index) => index + 1);
          setSequenceKey((key) => key + 1);
          return 0;
        }

        setPlaying(false);
        return 100;
      });
    }, TICK_MS);

    return () => window.clearInterval(interval);
  }, [playing, scene]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches('input, textarea, select')) return;

      if (event.code === 'Space') {
        event.preventDefault();
        togglePlayback();
      } else if (event.key === 'ArrowRight') {
        goToScene(scene + 1);
      } else if (event.key === 'ArrowLeft') {
        goToScene(scene - 1);
      } else if (event.key.toLowerCase() === 'r') {
        reset();
      } else if (event.key.toLowerCase() === 'f') {
        void toggleFullscreen();
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [goToScene, reset, scene, toggleFullscreen, togglePlayback]);

  return (
    <main className="forge-app" data-scene={scene + 1}>
      <div className="forge-grid" aria-hidden="true" />
      <div className="forge-aurora" aria-hidden="true" />
      <div className="forge-grain" aria-hidden="true" />

      <header className="forge-header">
        <div className="forge-brand">
          <div className="forge-logo" aria-hidden="true"><span>MF</span></div>
          <div>
            <p className="forge-brand-name">MEMORY FORGE</p>
            <p className="forge-brand-subtitle">WORLD GENERATION ENGINE</p>
          </div>
        </div>

        <div className="forge-mode-rail" aria-label="演示状态">
          <span className={playing ? 'mode-dot is-live' : 'mode-dot'} />
          <span>{playing ? 'DIRECTOR · PLAYING' : started ? 'EXPLORE · PAUSED' : 'DIRECTOR · READY'}</span>
          <i />
          <span>TIME COMPRESSED</span>
        </div>

        <div className="forge-run-meta">
          <button type="button" className="forge-back-live" onClick={onExit}>← LIVE STUDIO</button>
          <span>{DEMO_META.runId}</span>
          <span className="forge-real-data">● RECORDED DATA</span>
          <span className="forge-review-badge">{DEMO_META.review}</span>
        </div>
      </header>

      <div className="forge-layout">
        <nav className="forge-stage-nav" aria-label="演示阶段">
          <p>PIPELINE</p>
          {SCENES.map((item, index) => (
            <button
              key={item.nav}
              type="button"
              className={index === scene ? 'forge-stage is-active' : index < scene ? 'forge-stage is-complete' : 'forge-stage'}
              aria-current={index === scene ? 'step' : undefined}
              onClick={() => goToScene(index)}
            >
              <span>0{index + 1}</span>
              <strong>{item.nav}</strong>
              <small>{index < scene ? 'DONE' : index === scene ? 'ACTIVE' : 'READY'}</small>
            </button>
          ))}
        </nav>

        <section className="forge-main-stage" aria-labelledby="scene-title">
          <div className="forge-scene-heading">
            <div>
              <p className="forge-eyebrow">{current.eyebrow}</p>
              <h1 id="scene-title">{current.title}</h1>
            </div>
            <span className="forge-scene-index">SCENE 0{scene + 1} / 05</span>
          </div>

          <div
            key={`${scene}-${sequenceKey}`}
            className={started ? 'forge-scene-canvas is-running' : 'forge-scene-canvas'}
          >
            {scene === 0 && <CouncilScene running={started} />}
            {scene === 1 && <WorldScene />}
            {scene === 2 && <QuestionForgeScene />}
            {scene === 3 && <GroundingGateScene />}
            {scene === 4 && <ArenaScene />}
          </div>
        </section>

        <aside className="forge-telemetry" aria-label="运行指标">
          <div className="telemetry-heading">
            <p className="forge-panel-label">SCENE TELEMETRY</p>
            <span>0{scene + 1}</span>
          </div>
          {current.metrics.map(([label, value, unit]) => (
            <Metric key={label} label={label} value={value} unit={unit} />
          ))}
          <div className="forge-integrity">
            <div><ShieldCheck size={14} /><span>INTEGRITY LAYER</span></div>
            <p>全部指标来自同一游戏 Run；只展示可核验产物，不使用装饰性计数。</p>
          </div>
          <div className="forge-shortcuts">
            <span>SPACE</span><b>播放 / 暂停</b>
            <span>← →</span><b>切换场景</b>
            <span>F</span><b>全屏</b>
          </div>
        </aside>
      </div>

      <footer className="forge-controls">
        <Button className="forge-primary-action" size="lg" onClick={togglePlayback}>
          {playing ? <Pause data-icon="inline-start" /> : <Play data-icon="inline-start" />}
          {!started ? '启动导演模式' : playing ? '暂停回放' : scene === 4 && progress >= 100 ? '重新播放' : '继续回放'}
        </Button>

        <div className="forge-timeline" aria-label="演示进度">
          {SCENES.map((item, index) => {
            const width = index < scene ? 100 : index === scene ? progress : 0;
            return (
              <button key={item.nav} type="button" onClick={() => goToScene(index)} aria-label={`前往${item.nav}`}>
                <span><i style={{ width: `${width}%` }} /></span>
                <b>0{index + 1}</b>
              </button>
            );
          })}
        </div>

        <p className="forge-status" aria-live="polite">
          <span>{playing ? 'RUNNING' : started ? 'PAUSED' : 'READY'}</span>
          {started ? current.status : '离线回放数据已装载 · 按 Space 开始'}
        </p>

        <div className="forge-utility-actions">
          <Button variant="ghost" size="icon" aria-label="上一幕" onClick={() => goToScene(scene - 1)} disabled={scene === 0}><ArrowLeft /></Button>
          <Button variant="ghost" size="icon" aria-label="下一幕" onClick={() => goToScene(scene + 1)} disabled={scene === SCENES.length - 1}><ArrowRight /></Button>
          <Button variant="ghost" size="icon" aria-label="重置演示" onClick={reset}><RotateCcw /></Button>
          <Button variant="ghost" size="icon" aria-label="进入或退出全屏" onClick={() => void toggleFullscreen()}><Maximize2 /></Button>
        </div>
      </footer>
    </main>
  );
}

function CouncilScene({ running }: { running: boolean }) {
  const [activeAgent, setActiveAgent] = useState(0);
  const active = COUNCIL_AGENTS[activeAgent];

  return (
    <div className={running ? 'council-stage is-running' : 'council-stage'}>
      <svg className="council-links" viewBox="0 0 720 520" aria-hidden="true">
        <defs>
          <linearGradient id="council-line" x1="0" x2="1">
            <stop offset="0" stopColor="#7b8793" stopOpacity=".15" />
            <stop offset=".55" stopColor="#ff8a4c" stopOpacity=".9" />
            <stop offset="1" stopColor="#f7f8f5" stopOpacity=".3" />
          </linearGradient>
        </defs>
        {COUNCIL_AGENTS.map((agent) => {
          const radian = (agent.angle * Math.PI) / 180;
          const x = 360 + Math.cos(radian) * 246;
          const y = 260 + Math.sin(radian) * 202;
          return <line key={agent.id} x1={x} y1={y} x2="360" y2="260" />;
        })}
      </svg>

      <div className="council-rings" aria-hidden="true"><i /><i /><i /><i /></div>

      {COUNCIL_AGENTS.map((agent, index) => {
        const radian = (agent.angle * Math.PI) / 180;
        const x = 50 + Math.cos(radian) * 39;
        const y = 50 + Math.sin(radian) * 39;
        return (
          <button
            type="button"
            className={index === activeAgent ? 'agent-node is-selected' : 'agent-node'}
            key={agent.id}
            style={{ left: `${x}%`, top: `${y}%`, animationDelay: `${index * 120}ms` }}
            onClick={() => setActiveAgent(index)}
            aria-label={`${agent.id}：${agent.label}`}
          >
            <i>0{index + 1}</i>
            <strong>{agent.id}</strong>
            <span>{agent.label}</span>
          </button>
        );
      })}

      <div className="whitepaper-core">
        <div className="whitepaper-topline">
          <span>WORLD BLUEPRINT</span><em>{running ? 'ASSEMBLING' : 'STANDBY'}</em>
        </div>
        <div className="paper-title-row"><i>01</i><h2>霜狼之牙：被提前记录的死亡</h2></div>
        <div className="whitepaper-section"><span>WORLD GRAPH</span><b>31 nodes · 7 causal events</b></div>
        <div className="whitepaper-section"><span>STORY ARC</span><b>6 chapters · one protagonist</b></div>
        <div className="whitepaper-section"><span>CAPABILITIES</span><b>L1 · L2 · L3 · L5 · L6 · L7</b></div>
        <div className="whitepaper-section"><span>EVIDENCE</span><b>30 docs · 4 authority tiers</b></div>
        <div className="whitepaper-scan" />
        <div className="paper-corner" aria-hidden="true" />
      </div>

      <div className="agent-inspector">
        <span>{active.id} / {active.label}</span>
        <p>{active.summary}</p>
        <i><Sparkles size={12} /> STRUCTURED OUTPUT ONLY</i>
      </div>

      <p className="scene-caption">从核心悖论到证据法则，中央白皮书成为后续世界生成的唯一规格。</p>
    </div>
  );
}

function WorldScene() {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [week, setWeek] = useState(3);
  const selected = WORLD_SERIES[selectedIndex];
  const chapterTitles = ['未拆封的已完成任务', '死者在钟楼等他', '档案馆里的第二把刀', '世界追上那场死亡', '把清白烧进熔炉', '被世界误记着离开'];
  const sessionCount = selected.p0.length;
  const points = useMemo(() => {
    const values = selected.p0;
    const min = Math.min(...values);
    const max = Math.max(...values);
    return values.map((value, index) => {
      const x = 20 + index * (420 / (values.length - 1));
      const ratio = max === min ? 0.5 : (value - min) / (max - min);
      const y = 104 - ratio * 76;
      return `${x},${y}`;
    }).join(' ');
  }, [selected]);

  const nodes = [
    { label: '艾尔文', x: 12, y: 16, type: 'dept', index: 0 },
    { label: '伊瑟拉', x: 77, y: 18, type: 'dept', index: 1 },
    { label: '莉安娜', x: 18, y: 70, type: 'dept', index: 2 },
    { label: '冬眠钟', x: 76, y: 72, type: 'dept', index: 3 },
    { label: '银鹿议会', x: 35, y: 27, type: 'person' },
    { label: '死亡登记', x: 59, y: 28, type: 'person' },
    { label: '冬律原册', x: 35, y: 61, type: 'person' },
    { label: '霜狼之牙', x: 59, y: 62, type: 'person' },
    { label: '白钟桥', x: 47, y: 45, type: 'person' },
  ] as const;

  const edges = [
    [17, 21, 39, 31], [82, 23, 63, 32], [23, 75, 39, 64], [80, 77, 63, 65],
    [42, 33, 50, 47], [61, 35, 53, 47], [42, 64, 50, 50], [61, 64, 53, 50],
    [40, 31, 59, 32], [40, 64, 59, 64],
  ];

  return (
    <div className="world-stage">
      <div className="world-map-panel">
        <div className="panel-topline"><span>WORLD GRAPH / CHAPTER {String(week).padStart(2, '0')}</span><em>31 NODES · 7 EVENTS</em></div>
        <div className="world-kernel">
          <svg className="world-edges" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {edges.map((edge, index) => <line key={index} x1={edge[0]} y1={edge[1]} x2={edge[2]} y2={edge[3]} />)}
            <circle className="data-pulse pulse-a" r=".8"><animateMotion dur="4s" repeatCount="indefinite" path="M17 21 L39 31 L50 47 L61 35 L82 23" /></circle>
            <circle className="data-pulse pulse-b" r=".65"><animateMotion dur="5.5s" repeatCount="indefinite" path="M23 75 L39 64 L50 50 L61 64 L80 77" /></circle>
          </svg>
          <div className="world-core-orb" aria-hidden="true"><i /><span>C{String(week).padStart(2, '0')}</span></div>
          {nodes.map((node) => (
            <button
              key={node.label}
              type="button"
              className={`world-node is-${node.type} ${'index' in node && node.index === selectedIndex ? 'is-selected' : ''}`}
              style={{ left: `${node.x}%`, top: `${node.y}%` }}
              onClick={() => 'index' in node && setSelectedIndex(node.index)}
            >
              <span>{node.label}</span>
              <small>{node.type === 'dept' ? 'FOCUS NODE' : 'WORLD LINK'}</small>
            </button>
          ))}
        </div>
        <div className="week-scrubber">
          <div><span>2025-02-14</span><b>CHAPTER {String(week).padStart(2, '0')} / 06</b><span>2025-02-19</span></div>
          <input aria-label="选择故事章节" type="range" min="1" max={sessionCount} value={week} onChange={(event) => setWeek(Number(event.target.value))} />
          <div className="week-ticks">{Array.from({ length: sessionCount }, (_, index) => <i key={index} className={index + 1 <= week ? 'is-past' : ''}>C{index + 1}</i>)}</div>
        </div>
      </div>

      <div className="world-data-panel">
        <div className="panel-topline"><span>ENTITY INSPECTOR</span><em>CLICK NODES</em></div>
        <div className="entity-title"><i style={{ background: selected.color }} /><div><span>SELECTED ENTITY</span><h2>{selected.name}</h2></div></div>
        <div className="state-grid">
          <StateCell label="叙事张力" value={`${selected.p0[week - 1]}%`} delta={week > 1 ? selected.p0[week - 1] - selected.p0[week - 2] : 0} />
          <StateCell label="证据触点" value={String(selected.oncall[week - 1])} delta={week > 1 ? selected.oncall[week - 1] - selected.oncall[week - 2] : 0} />
          <StateCell label="真相可判度" value={`${selected.sla[week - 1]}%`} delta={week > 1 ? selected.sla[week - 1] - selected.sla[week - 2] : 0} />
          <StateCell label="CURRENT BEAT" value={`第 ${week} 章`} />
        </div>
        <div className="mini-chart">
          <div><span>NARRATIVE TENSION / 6 CHAPTERS</span><b>{selected.p0[0]} → {selected.p0[sessionCount - 1]}%</b></div>
          <svg viewBox="0 0 460 120" aria-label={`${selected.name}六章叙事张力`}>
            <title>{selected.name}六章叙事张力</title>
            <path d="M20 104 H440 M20 66 H440 M20 28 H440" />
            <polyline points={points} style={{ stroke: selected.color }} />
            {points.split(' ').map((point, index) => {
              const [x, y] = point.split(',');
              return <circle key={index} cx={x} cy={y} r={index + 1 === week ? 5 : 2.5} className={index + 1 === week ? 'is-current' : ''} style={{ stroke: selected.color }} />;
            })}
          </svg>
        </div>
        <div className="world-event-log"><Zap size={14} /><span>STORY EVENT</span><p>C{week} · {chapterTitles[week - 1]}</p></div>
      </div>
    </div>
  );
}

function StateCell({ label, value, delta }: { label: string; value: string; delta?: number }) {
  return (
    <div className="state-cell">
      <span>{label}</span>
      <strong>{value}</strong>
      {delta !== undefined && <small className={delta > 0 ? 'is-up' : delta < 0 ? 'is-down' : ''}>{delta > 0 ? '+' : ''}{delta.toFixed(1)} Δ</small>}
    </div>
  );
}

function QuestionForgeScene() {
  const [selected, setSelected] = useState(0);
  const question = SPOTLIGHT_QUESTIONS[selected];

  return (
    <div className="question-stage">
      <div className="capability-panel">
        <div className="panel-topline"><span>CAPABILITY DISPATCH</span><em>6 ACTIVE / 18 GROUNDED</em></div>
        <div className="channel-grid">
          {CAPABILITY_CHANNELS.map((channel, index) => (
            <div key={channel.id} className={channel.status === 'materialized' ? 'channel is-live' : 'channel is-empty'} style={{ animationDelay: `${index * 90}ms` }}>
              <div><b>{channel.id}</b><span>{channel.name}</span><em>{channel.status === 'materialized' ? `${channel.grounded}/${channel.candidates}` : 'SCANNED'}</em></div>
              <i><span style={{ width: channel.candidates ? `${(channel.grounded / channel.candidates) * 100}%` : '100%' }} /></i>
            </div>
          ))}
        </div>
        <p className="dispatch-note"><span>6 / 6 ACTIVE</span> 只保留能被这个世界真实支撑的能力线路。</p>
      </div>

      <div className="forge-portal-panel">
        <div className="forge-portal" aria-hidden="true">
          <i /><i /><i />
          <div><span>QUESTION</span><strong>18</strong><b>GROUNDED</b></div>
        </div>
        <div className="doc-stream" aria-hidden="true">
          {['死亡簿 E-0211', '灰隘关签押簿', '白钟桥现场报告', '冬眠钟核心遥测'].map((doc, index) => <span key={doc} style={{ animationDelay: `${index * 420}ms` }}>{doc}</span>)}
        </div>
        <div className="question-stack">
          {SPOTLIGHT_QUESTIONS.map((item, index) => (
            <button
              key={item.id}
              type="button"
              className={index === selected ? 'question-card is-selected' : 'question-card'}
              style={{ animationDelay: `${400 + index * 260}ms` }}
              onClick={() => setSelected(index)}
            >
              <span>{item.id}</span>
              <b>{item.line}</b>
              <p>{item.text}</p>
              <i>{item.capability}</i>
            </button>
          ))}
        </div>
      </div>

      <div className="question-inspector">
        <div className="panel-topline"><span>QUESTION ANATOMY</span><em>{question.id}</em></div>
        <div className="question-number"><span>0{selected + 1}</span><i>SPOTLIGHT</i></div>
        <h2>{question.text}</h2>
        <div className="trace-chain">
          {question.trace.map((step, index) => (
            <div key={`${step}-${index}`}><span>{step}</span>{index < question.trace.length - 1 && <ChevronRight />}</div>
          ))}
        </div>
        <div className="anatomy-meta"><span>CAPABILITY</span><b>{question.capability}</b></div>
        <div className="anatomy-meta"><span>GROUND TRUTH</span><b>MECHANICAL POINTER</b></div>
        <p className="question-disclaimer">镜头只公开问题表面与能力标签；机械真值、证据 Session 和辅助字段不进入前端包。</p>
      </div>
    </div>
  );
}

function GroundingGateScene() {
  const [selected, setSelected] = useState(0);
  const item = GATE_CASES[selected];

  return (
    <div className="gate-stage">
      <div className="gate-feed">
        <div className="panel-topline"><span>CANDIDATE FEED</span><em>HISTORICAL REPLAY</em></div>
        {GATE_CASES.map((entry, index) => (
          <button key={entry.id} type="button" className={`${entry.state} ${index === selected ? 'is-selected' : ''}`} onClick={() => setSelected(index)}>
            <span>{entry.id}</span><b>{entry.label}</b><p>{entry.question}</p><i>{entry.verdict}</i>
          </button>
        ))}
        <div className="gate-counters">
          <div><span>INPUT</span><strong>18</strong></div><i>→</i>
          <div className="survivor"><span>GROUNDED</span><strong>18</strong></div><i>+</i>
          <div className="rejected"><span>REJECT</span><strong>0</strong></div>
        </div>
      </div>

      <div className={`grounding-core is-${item.state}`}>
        <div className="gate-halo" aria-hidden="true"><i /><i /><i /></div>
        <svg viewBox="0 0 600 440" className="grounding-triangle" aria-hidden="true">
          <defs>
            <linearGradient id="gate-line" x1="0" x2="1"><stop stopColor="#ff8a4c" /><stop offset="1" stopColor="#eff4f6" /></linearGradient>
          </defs>
          <path d="M300 72 L105 350 L495 350 Z" />
          <path className="gate-trace" d="M300 72 L105 350 L495 350 Z" />
        </svg>
        <div className="gate-vertex vertex-question"><span>01</span><b>QUESTION</b><p>问题表面可回答</p>{item.checks[0] ? <Check /> : <X />}</div>
        <div className="gate-vertex vertex-truth"><span>02</span><b>MECHANICAL GT</b><p>真值指针唯一</p>{item.checks[1] ? <Check /> : <X />}</div>
        <div className="gate-vertex vertex-evidence"><span>03</span><b>EVIDENCE</b><p>证据链可定位</p>{item.checks[2] ? <Check /> : <X />}</div>
        <div className="gate-verdict">
          {item.state === 'pass' ? <Check /> : <ShieldCheck />}
          <span>{item.label}</span>
          <strong>{item.state === 'pass' ? 'GROUNDING PASS' : 'JUSTIFIED REFUSAL'}</strong>
        </div>
      </div>

      <div className="evidence-panel">
        <div className="panel-topline"><span>AUDIT TRACE</span><em>{item.id}</em></div>
        <h2>{item.question}</h2>
        <p className={`verdict-copy is-${item.state}`}>{item.verdict}</p>
        <div className="evidence-list">
          {item.evidence.map((entry, index) => <div key={entry}><span>0{index + 1}</span><p>{entry}</p><b>{item.checks[Math.min(index + 1, 2)] ? 'VERIFIED' : 'MISSING'}</b></div>)}
        </div>
        <div className="gate-version"><span>GROUNDING GATE V0</span><p>18/18 良定义，18/18 证据接地；所有答案均能回到实际语料。</p></div>
      </div>
    </div>
  );
}

function ArenaScene() {
  const [selected, setSelected] = useState(1);
  const system = ARENA_SYSTEMS[selected];

  return (
    <div className="arena-stage">
      <div className="arena-query">
        <div className="query-id"><span>SEALED</span><i>GAME WORLD BENCHMARK</i></div>
        <p>霜狼之牙：被提前记录的死亡</p>
        <div className="query-beam" aria-hidden="true"><i /><i /><i /></div>
      </div>

      <div className="system-track">
        {ARENA_SYSTEMS.map((item, index) => {
          return (
            <button key={item.id} type="button" className={index === selected ? 'system-runner is-selected' : 'system-runner'} onClick={() => setSelected(index)} style={{ animationDelay: `${index * 130}ms` }}>
              <span style={{ borderColor: item.color, color: item.color }}>{item.id.toUpperCase()}</span>
              <div><b>{item.name}</b><small>{item.role}</small></div>
              <i className="is-pass"><Check /></i>
            </button>
          );
        })}
      </div>

      <div className="arena-inspector">
        <div className="panel-topline"><span>CAPABILITY TRACE</span><em>{system.id.toUpperCase()}</em></div>
        <div className="system-title"><i style={{ background: system.color }} /><div><span>SELECTED CAPABILITY</span><h2>{system.name}</h2></div></div>
        <div className="arena-pipeline">
          <div><span>01</span><b>QUESTIONS</b><i>{system.questions} FORGED</i></div>
          <div><span>02</span><b>STAR QUESTIONS</b><i>{system.stars} SPOTLIGHT</i></div>
          <div><span>03</span><b>GROUNDING</b><i>{system.questions}/{system.questions} PASS</i></div>
        </div>
        <div className="arena-result is-pass">
          <Check /><div><span>CAPABILITY VERDICT</span><strong>SEALED</strong></div>
        </div>
        <div className="diagnostic-score"><span>QUESTION COVERAGE</span><strong>{system.questions} ITEMS</strong><i><b style={{ width: `${(system.questions / 5) * 100}%`, background: system.color }} /></i></div>
      </div>

      <div className="fingerprint-panel">
        <div className="panel-topline"><span>CAPABILITY COVERAGE</span><em>18 QUESTIONS · 6 STARS</em></div>
        <div className="fingerprint-header"><span />{ARENA_SYSTEMS.map((item) => <b key={item.id}>{item.id.toUpperCase()}</b>)}</div>
        <div className="fingerprint-grid">
          {CAPABILITY_FINGERPRINT.map((row) => (
            <div key={row.key} className="fingerprint-row">
              <span>{row.key}</span>
              {row.values.map((value, index) => <i key={index} style={{ '--score': Math.max(value / 5, .08), '--cell-color': ARENA_SYSTEMS[index].color } as React.CSSProperties}><b>{value}</b></i>)}
            </div>
          ))}
        </div>
        <div className="arena-thesis"><Sparkles /><p><span>它构建的，不是一段文本。</span>而是一个会运转、会留下证据、也会被追问的世界。</p></div>
        <p className="arena-disclaimer">GAME SHOWCASE · 31 WORLD NODES · 30 EVIDENCE DOCS · 18/18 WELL-POSED · 18/18 GROUNDED</p>
      </div>
    </div>
  );
}

function Metric({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <div className="forge-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <em>{unit}</em>
    </div>
  );
}
