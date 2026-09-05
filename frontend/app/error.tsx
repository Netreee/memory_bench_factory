'use client';

import { RotateCcw, ShieldAlert } from 'lucide-react';

import { Button } from '@/components/ui/button';

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="recovery-page">
      <div className="recovery-grid" aria-hidden="true" />
      <section>
        <ShieldAlert />
        <p>DETERMINISTIC REPLAY INTERRUPTED</p>
        <h1>回放链路暂时中断</h1>
        <span>本 Demo 使用本地脱敏数据，不会因重试产生额外写入。</span>
        <Button onClick={reset}><RotateCcw />重新装载回放</Button>
      </section>
    </main>
  );
}
