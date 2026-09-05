import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="recovery-page">
      <div className="recovery-grid" aria-hidden="true" />
      <section>
        <span className="recovery-code">404</span>
        <p>SCENE NOT FOUND</p>
        <h1>这个记忆世界不存在</h1>
        <span>返回导演回放，重新选择五幕中的一个场景。</span>
        <Link href="/"><ArrowLeft />回到 Memory Forge</Link>
      </section>
    </main>
  );
}
