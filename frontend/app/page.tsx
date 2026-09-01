'use client';

import { useState } from 'react';
import { Maximize2, Play, RotateCcw } from 'lucide-react';

import { Button } from '@/components/ui/button';

const agents = [
  { name: 'OBSERVER', label: '领域观测', angle: -90 },
  { name: 'SKEPTIC', label: '风险质疑', angle: -30 },
  { name: 'MAPPER', label: '能力映射', angle: 30 },
  { name: 'MEDIUM', label: '媒介设计', angle: 90 },
  { name: 'STYLIST', label: '语言纹理', angle: 150 },
  { name: 'TRAPPER', label: '陷阱布局', angle: 210 },
];

const stages = ['智能体议会', '世界构建', '问题铸造', '证据校验', '记忆竞技场'];

export default function Home() {
  const [started, setStarted] = useState(false);

  return (
    <main className="forge-app">
      <div className="forge-grid" aria-hidden="true" />
      <header className="forge-header">
        <div className="forge-brand">
          <div className="forge-logo">MF</div>
          <div>
            <p className="forge-brand-name">MEMORY FORGE</p>
            <p className="forge-brand-subtitle">BENCHMARK SYNTHESIS DECK</p>
          </div>
        </div>
        <div className="forge-run-meta">
          <span>DIAGNOSTIC REPLAY / OFFICE 064826</span>
          <span className="forge-real-data">● REAL DATA</span>
        </div>
      </header>

      <div className="forge-layout">
        <nav className="forge-stage-nav" aria-label="演示阶段">
          {stages.map((stage, index) => (
            <button
              key={stage}
              type="button"
              className={index === 0 ? 'forge-stage is-active' : 'forge-stage'}
              aria-current={index === 0 ? 'step' : undefined}
            >
              <span>0{index + 1}</span>
              <strong>{stage}</strong>
            </button>
          ))}
        </nav>

        <section className="forge-main-stage" aria-labelledby="scene-title">
          <div className="forge-scene-heading">
            <div>
              <p className="forge-eyebrow">COUNCIL ONLINE · 6 PARALLEL AGENTS</p>
              <h1 id="scene-title">多智能体将一个场景，锻造成可执行白皮书</h1>
            </div>
            <span className="forge-scene-index">SCENE 01 / 05</span>
          </div>

          <div className={started ? 'council-stage is-running' : 'council-stage'}>
            <svg className="council-links" viewBox="0 0 620 500" aria-hidden="true">
              {agents.map((agent) => {
                const radian = (agent.angle * Math.PI) / 180;
                const x = 310 + Math.cos(radian) * 190;
                const y = 250 + Math.sin(radian) * 190;
                return <line key={agent.name} x1={x} y1={y} x2="310" y2="250" />;
              })}
            </svg>
            <div className="council-rings" aria-hidden="true"><i /><i /><i /></div>
            {agents.map((agent, index) => {
              const radian = (agent.angle * Math.PI) / 180;
              const x = 50 + Math.cos(radian) * 36;
              const y = 50 + Math.sin(radian) * 38;
              return (
                <button
                  type="button"
                  className="agent-node"
                  key={agent.name}
                  style={{ left: `${x}%`, top: `${y}%`, animationDelay: `${index * 100}ms` }}
                  aria-label={`${agent.name}：${agent.label}`}
                >
                  <strong>{agent.name}</strong>
                  <span>{agent.label}</span>
                </button>
              );
            })}
            <div className="whitepaper-core">
              <div className="whitepaper-topline">
                <span>01_WHITEPAPER</span><em>{started ? 'COMPILING' : 'STANDBY'}</em>
              </div>
              <h2>Office memory world</h2>
              <div className="whitepaper-section"><span>SCHEMA</span><b>entities · fields · relations</b></div>
              <div className="whitepaper-section"><span>DYNAMICS</span><b>10 weeks · state transitions</b></div>
              <div className="whitepaper-section"><span>CAPABILITIES</span><b>L1 — L10 production lines</b></div>
              <div className="whitepaper-scan" />
            </div>
            <p className="council-caption">
              {started ? '六个角色的结构化判断正在汇聚；中央 Critic 即将锁定规格。' : '点击“启动全链路”，从真实历史 Run 开始确定性回放。'}
            </p>
          </div>
        </section>

        <aside className="forge-telemetry" aria-label="运行指标">
          <p className="forge-panel-label">LIVE TELEMETRY</p>
          <Metric label="PARALLEL AGENTS" value="06" unit={started ? 'ACTIVE' : 'READY'} />
          <Metric label="LLM CALLS" value="1175" unit="TRACE" />
          <Metric label="ARTIFACT" value="01" unit="WHITEPAPER" />
          <div className="forge-integrity">
            <span>INTEGRITY NOTE</span>
            <p>只展示角色、任务和结构化产物，不伪造模型思维链。</p>
          </div>
        </aside>
      </div>

      <footer className="forge-controls">
        <Button className="forge-primary-action" size="lg" onClick={() => setStarted(true)}>
          <Play data-icon="inline-start" />启动全链路
        </Button>
        <div className="forge-timeline" aria-label="演示进度">
          {stages.map((stage, index) => <span key={stage} className={index === 0 ? 'is-current' : ''} />)}
        </div>
        <p className="forge-status" aria-live="polite">{started ? 'SCENE 01 · 白皮书正在构建' : 'DEMO READY · 离线数据已装载'}</p>
        <div className="forge-utility-actions">
          <Button variant="ghost" size="icon" aria-label="重置演示" onClick={() => setStarted(false)}><RotateCcw /></Button>
          <Button variant="ghost" size="icon" aria-label="进入全屏"><Maximize2 /></Button>
        </div>
      </footer>
    </main>
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
