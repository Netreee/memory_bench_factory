import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';

import './globals.css';

const geistSans = Geist({ variable: '--font-geist-sans', subsets: ['latin'] });
const geistMono = Geist_Mono({ variable: '--font-geist-mono', subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'Memory Forge · 从场景到可验证的记忆世界',
  description: '一段 60 秒的交互式诊断回放：白皮书、时间世界、问题铸造、证据闸门与六系统记忆竞技场。',
  openGraph: {
    title: 'Memory Forge · 从场景到可验证的记忆世界',
    description: '从一句场景，锻造可验证的记忆世界。',
    type: 'website',
    locale: 'zh_CN',
    images: [{ url: '/memory-forge-social-preview.png', width: 1672, height: 941, alt: 'Memory Forge 世界内核' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Memory Forge',
    description: '从一句场景，到可验证的记忆世界。',
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
