export type QualitySnapshot = {
  version: number;
  status: 'not_run' | 'passed' | 'failed' | 'stale';
  eligible: boolean;
  scope: string[];
  checks: Record<string, unknown>;
  issues: Array<Record<string, unknown>>;
};

export function unverifiedQuality(): QualitySnapshot {
  return { version: 1, status: 'not_run', eligible: false, scope: [], checks: {}, issues: [] };
}

export function qualityLabel(quality: QualitySnapshot | undefined): string {
  if (!quality) return '未验收';
  if (quality.status === 'passed' && quality.eligible === true) return '合格';
  return { not_run: '未验收', passed: '未取得发布资格', failed: '未通过', stale: '验收已失效' }[quality.status] ?? '未验收';
}

export function formatCount(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '未测';
}

export function replayPath(runId: string): string {
  if (!/^[A-Za-z0-9_-]{6,96}$/.test(runId)) throw new Error('非法 Run ID');
  return `/api/replay/${encodeURIComponent(runId)}`;
}
