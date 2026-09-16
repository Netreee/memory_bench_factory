import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';

import './globals.css';

const geistSans = Geist({ variable: '--font-geist-sans', subsets: ['latin'] });
const geistMono = Geist_Mono({ variable: '--font-geist-mono', subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'Memory Forge · 从场景到可验证的世界 Benchmark',
  description: '以《霜狼之牙：被提前记录的死亡》为例，展示世界白皮书、因果世界、问题铸造与证据接地。',
  openGraph: {
    title: 'Memory Forge · 从场景到可验证的世界 Benchmark',
    description: '给它一个场景；它构建一个会运转、会留下证据、也会被追问的世界。',
    type: 'website',
    locale: 'zh_CN',
    images: [{ url: '/memory-forge-social-preview.png', width: 1672, height: 941, alt: 'Memory Forge 世界内核' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Memory Forge',
    description: '从一句场景，到一个可追问、可验证的世界 Benchmark。',
    images: ['/memory-forge-social-preview.png'],
  },
  icons: { icon: '/favicon.svg' },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" className="dark">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>{children}</body>
    </html>
  );
}
