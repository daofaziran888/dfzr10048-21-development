import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ===== 置信复核补审工作台 =================================================

export type WorkbenchResult = {
  version: string
  items: EntryRow[]
  missing: EntryRow[]
  reviewing: EntryRow[]
  rejected: EntryRow[]
  passed: EntryRow[]
  missing_count: number
  reviewing_count: number
  rejected_count: number
  passed_count: number
  [key: string]: unknown
}

export async function fetchWorkbench(version: string): Promise<WorkbenchResult> {
  const query = new URLSearchParams({ version }).toString()
  const response = await request(`/api/review-workbench?${query}`)
  if (!response.ok) throw new Error('补审工作台读取失败')
  return await response.json()
}

export async function postWorkbenchAction(
  path: 'claim' | 'submit' | 'resegment',
  body: Record<string, unknown>,
): Promise<ActionResult & Record<string, unknown>> {
  const response = await request(`/api/review-workbench/${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return await response.json()
}

export async function fetchEligibility(version: string): Promise<Record<string, any>> {
  const query = new URLSearchParams({ version }).toString()
  const response = await request(`/api/release?${query}`)
  if (!response.ok) throw new Error('发布资格核验失败')
  return await response.json()
}

export async function fetchSegmentHistory(segment: string): Promise<Record<string, any>> {
  const query = new URLSearchParams({ segment }).toString()
  const response = await request(`/api/review-workbench/history?${query}`)
  if (!response.ok) throw new Error('分段历史读取失败')
  return await response.json()
}

export async function writeOffAppeal(
  appealRef: string,
  body: Record<string, unknown> = {},
): Promise<ActionResult & Record<string, unknown>> {
  const response = await request(`/api/dictation_appeal/${appealRef}/write-off`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return await response.json()
}

export async function continueAppeal(
  appealRef: string,
): Promise<ActionResult & Record<string, unknown>> {
  const response = await request(`/api/dictation_appeal/${appealRef}/continue`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
  return await response.json()
}

export async function locateAppeal(appealRef: string): Promise<Record<string, any>> {
  const response = await request(`/api/dictation_appeal/${appealRef}/locate`)
  if (!response.ok) throw new Error('重听定位失败')
  return await response.json()
}
