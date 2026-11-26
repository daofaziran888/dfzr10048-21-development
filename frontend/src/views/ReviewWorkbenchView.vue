<template>
  <section class="page review-desk" data-module="review_workbench">
    <header class="page-head">
      <div>
        <h2>置信复核 · 补审工作台</h2>
        <p class="page-desc">
          按转写版本查看缺签分段，按语音时间排序并定位；复核员从清单领段、凭原单交签，
          服务端返回补审结论并核验版本发布资格。缺审与拒绝分开，V1 的签名不能补 V2。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="refresh" type="button" @click="reload">刷新清单</button>
      </div>
    </header>

    <!-- 版本与复核员 -->
    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>转写版本</span>
        <select v-model="version" data-testid="version-select" @change="reload">
          <option v-for="v in versions" :key="String(v.id)" :value="String(v['版本号'])">
            {{ v['版本号'] }}（{{ v['版本编号'] }} · {{ v['关联任务'] }}）
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>复核员标识</span>
        <input v-model="reviewer" data-testid="reviewer-input" placeholder="如 reviewer_jia" />
      </label>
      <button class="btn primary" type="submit">按版本查询</button>
      <button class="btn ghost" type="button" @click="toggleSort">
        语音时间：{{ sortAsc ? '从早到晚 ↑' : '从晚到早 ↓' }}
      </button>
      <label class="filter-item inline-check">
        <input type="checkbox" v-model="showHistorical" data-testid="show-history" />
        <span>显示已重切历史段</span>
      </label>
    </form>

    <div v-if="banner" class="banner" :class="banner.kind" data-testid="banner">
      <span>{{ banner.text }}</span>
      <button class="link" type="button" @click="banner = null">知道了</button>
    </div>
    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>

    <!-- 分桶统计 -->
    <div class="stat-row" data-testid="bucket-stats">
      <article class="stat-card bucket" :class="{ active: tab === '缺审' }" @click="tab = '缺审'">
        <span class="stat-label">缺审 / 缺签</span>
        <strong class="stat-value" data-testid="count-missing">{{ bench?.missing_count ?? 0 }}</strong>
      </article>
      <article class="stat-card bucket" :class="{ active: tab === '复核中' }" @click="tab = '复核中'">
        <span class="stat-label">已领取未交签</span>
        <strong class="stat-value">{{ bench?.reviewing_count ?? 0 }}</strong>
      </article>
      <article class="stat-card bucket reject" :class="{ active: tab === '已拒绝' }" @click="tab = '已拒绝'">
        <span class="stat-label">已拒绝（单独队列）</span>
        <strong class="stat-value" data-testid="count-rejected">{{ bench?.rejected_count ?? 0 }}</strong>
      </article>
      <article class="stat-card bucket pass" :class="{ active: tab === '已通过' }" @click="tab = '已通过'">
        <span class="stat-label">已通过签名</span>
        <strong class="stat-value">{{ bench?.passed_count ?? 0 }}</strong>
      </article>
      <article class="stat-card bucket" :class="{ active: tab === 'all' }" @click="tab = 'all'">
        <span class="stat-label">全部当前覆盖</span>
        <strong class="stat-value">{{ bench?.total ?? 0 }}</strong>
      </article>
    </div>

    <!-- 原单交签：重切后甲仍可从原单提交 -->
    <form class="original-form" data-testid="original-submit" @submit.prevent="submitOriginal">
      <strong>凭原补审单交签</strong>
      <input v-model="original.claimId" placeholder="补审单号，如 RCLM-0001" data-testid="original-claim" />
      <select v-model="original.conclusion" data-testid="original-conclusion">
        <option value="通过">结论：通过</option>
        <option value="拒绝">结论：拒绝</option>
      </select>
      <select v-model="original.legacy" data-testid="original-legacy">
        <option value="auto">重切旧签：按系统策略</option>
        <option value="accept">旧签名仍受理</option>
        <option value="reject">旧签不受理</option>
      </select>
      <button class="btn primary" type="submit" :disabled="busy">从原单提交</button>
    </form>

    <!-- 待办清单 -->
    <table class="data-table todo-table" data-testid="todo-table">
      <thead>
        <tr>
          <th>语音时间 / 定位</th>
          <th>分段编号</th>
          <th>段内文本（领取时快照可在历史查看）</th>
          <th>置信度</th>
          <th>分类</th>
          <th>领取人 / 单号</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in visibleItems"
          :key="String(row.segment_id)"
          :id="`seg-${row.id}`"
          :data-testid="`todo-row-${row.segment_id}`"
          :data-category="row.category"
          :class="{ located: locatedId === row.segment_id, historical: !row.current }"
        >
          <td class="time-cell">
            <button class="link" type="button" :data-testid="`locate-${row.segment_id}`" @click="locate(row)">
              {{ row.time_label }}
            </button>
            <div class="ms">{{ row.start_ms }}–{{ row.end_ms }}ms</div>
          </td>
          <td>
            {{ row.segment_id }}
            <span v-if="!row.current" class="badge hist">已重切·历史</span>
            <span v-if="row.must_sign" class="badge must">必签</span>
          </td>
          <td class="text-cell">{{ row['段内文本'] }}</td>
          <td :class="{ low: row.low_confidence }">
            {{ row['段置信度'] }}
            <div class="ms">阈值 {{ row.threshold }}</div>
          </td>
          <td><span class="badge" :class="categoryClass(row.category)">{{ row.category }}</span></td>
          <td>
            <div>{{ row.reviewer }}</div>
            <div class="ms">{{ ticketFor(row) || row.claim_id || '未领取' }}</div>
          </td>
          <td class="row-actions wrap">
            <button
              class="link" type="button"
              :data-testid="`claim-${row.segment_id}`"
              @click="claim(row)"
            >领取</button>
            <button
              class="link pass" type="button"
              :data-testid="`pass-${row.segment_id}`"
              @click="quickSubmit(row, '通过')"
            >通过交签</button>
            <button
              class="link reject" type="button"
              :data-testid="`reject-${row.segment_id}`"
              @click="quickSubmit(row, '拒绝')"
            >拒绝</button>
            <button class="link" type="button" @click="openReseg(row)">更正重切</button>
            <button class="link" type="button" @click="showHistory(row)">历史</button>
          </td>
        </tr>
        <tr v-if="!visibleItems.length">
          <td colspan="7" class="empty-state">该版本当前分类下没有分段</td>
        </tr>
      </tbody>
    </table>

    <!-- 定位结果 -->
    <div v-if="location" class="location-card" data-testid="location-card">
      <strong>定位：</strong>
      实际需要重听的位置为
      <code>{{ location.segment_id }}</code>
      起 <code>{{ location.time_label }}</code>（{{ location.position_ms }}ms，
      区间 {{ location.start_ms }}–{{ location.end_ms }}ms）
      <span class="ms">{{ location.note }}</span>
    </div>

    <!-- 发布资格 / 制作人发布页 -->
    <section class="release-card" data-testid="release-page">
      <div class="release-head">
        <h3>制作人发布页 · {{ version }} 覆盖与发布资格</h3>
        <button class="btn primary" type="button" data-testid="verify-release" @click="verifyRelease">
          核验发布资格
        </button>
      </div>
      <div v-if="eligibility" class="release-body">
        <p>
          资格结论：
          <span class="badge big" :class="eligibility.eligible ? 'pass' : 'reject'" data-testid="eligibility-badge">
            {{ eligibility.eligible ? '具备发布资格' : '暂不具备发布资格' }}
          </span>
          <span class="ms">{{ eligibility.message }}</span>
        </p>
        <p class="ms">{{ eligibility.rule }}</p>
        <table class="data-table">
          <thead>
            <tr><th>当前覆盖分段</th><th>语音时间</th><th>文本</th><th>签名状态</th><th>复核编号</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in eligibility.coverage" :key="c.segment_id">
              <td>{{ c.segment_id }}</td>
              <td>{{ c.time_label }}</td>
              <td class="text-cell">{{ c.text }}</td>
              <td><span class="badge" :class="categoryClass(c.category)">{{ c.category }}</span></td>
              <td>{{ c.effective_review_id || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="eligibility.blockers?.length" class="blockers" data-testid="blockers">
          <strong>阻断项：</strong>
          <ul>
            <li v-for="(b, i) in eligibility.blockers" :key="i">{{ b.reason }}</li>
          </ul>
        </div>
      </div>
    </section>

    <!-- 听写申诉 -->
    <section class="appeal-card" data-testid="appeal-panel">
      <div class="release-head">
        <h3>听写申诉核查</h3>
      </div>
      <form class="filter-bar" @submit.prevent="createAppeal">
        <label class="filter-item"><span>关联分段</span>
          <input v-model="appealForm.segment" placeholder="SPCH-0004" /></label>
        <label class="filter-item"><span>位置毫秒</span>
          <input v-model="appealForm.position_ms" placeholder="13200" /></label>
        <label class="filter-item grow"><span>申诉内容</span>
          <input v-model="appealForm.reason" placeholder="听感与字幕不一致…" /></label>
        <button class="btn" type="submit">登记申诉</button>
      </form>
      <table class="data-table">
        <thead>
          <tr><th>申诉编号</th><th>旧分段 / 位置</th><th>内容</th><th>状态</th><th>核销复核</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="a in appeals" :key="String(a.id)" :data-testid="`appeal-row-${a['申诉编号']}`">
            <td>{{ a['申诉编号'] }}</td>
            <td>{{ a['关联分段'] }}<div class="ms">{{ a['申诉位置毫秒'] }}ms</div></td>
            <td class="text-cell">{{ a['申诉内容'] }}</td>
            <td><span class="badge" :class="appealClass(a.status)">{{ a.status }}</span></td>
            <td>
              <input v-model="writeoffIds[String(a['申诉编号'])]" placeholder="复核编号(可空)" class="mini-input" />
              <div class="ms">{{ a['核销复核编号'] }}</div>
              <div v-if="appealLocations[String(a['申诉编号'])]" class="loc">
                → {{ appealLocations[String(a['申诉编号'])].segment_id }}
                @ {{ appealLocations[String(a['申诉编号'])].time_label }}
              </div>
            </td>
            <td class="row-actions wrap">
              <button class="link" type="button" @click="locateAppealRow(a)">定位重听</button>
              <button class="link pass" type="button" data-testid="appeal-writeoff" @click="writeOff(a)">
                凭历史复核核销
              </button>
              <button class="link" type="button" data-testid="appeal-continue" @click="continueRow(a)">
                续办
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 更正重切弹层 -->
    <div v-if="reseg.show" class="modal-backdrop" data-testid="reseg-modal" @click.self="reseg.show = false">
      <form class="modal" @submit.prevent="submitReseg">
        <h3>分段更正并重切 · {{ reseg.segmentId }}</h3>
        <label class="create-field"><span>切分点毫秒（多个用逗号，留空则整段更正）</span>
          <input v-model="reseg.splitAt" placeholder="例如 13800" /></label>
        <label class="create-field"><span>整段新起始毫秒（无切分点时生效，可留空）</span>
          <input v-model="reseg.start_ms" /></label>
        <label class="create-field"><span>整段新结束毫秒</span>
          <input v-model="reseg.end_ms" /></label>
        <label class="create-field"><span>纠正文本（整段替换时使用）</span>
          <input v-model="reseg.corrected_text" placeholder="更正后的文本" /></label>
        <p class="ms">重切后旧分段与旧文本保留可查，已领取未交签的原单仍可提交。</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="reseg.show = false">取消</button>
          <button class="btn primary" type="submit">确认重切</button>
        </div>
      </form>
    </div>

    <!-- 历史弹层 -->
    <div v-if="history.show" class="modal-backdrop" data-testid="history-modal" @click.self="history.show = false">
      <div class="modal">
        <h3>分段谱系与历史 · {{ history.segmentId }}</h3>
        <p><strong>旧文本：</strong>{{ history.data?.old_text }}</p>
        <h4>分段</h4>
        <ul>
          <li v-for="s in history.data?.segments ?? []" :key="String(s['分段编号'])">
            {{ s['分段编号'] }} [{{ s['起始毫秒'] }}–{{ s['结束毫秒'] }}ms]
            {{ s['覆盖状态'] }}：{{ s['段内文本'] }}
          </li>
        </ul>
        <h4>领取记录</h4>
        <ul>
          <li v-for="c in history.data?.claims ?? []" :key="String(c['补审单号'])">
            {{ c['补审单号'] }} · {{ c['复核人标识'] }} · {{ c.status }} · {{ c['领取时间'] }}
          </li>
        </ul>
        <h4>复核结论</h4>
        <ul>
          <li v-for="r in history.data?.reviews ?? []" :key="String(r['复核编号'])">
            {{ r['复核编号'] }} · {{ r['复核结论'] }} · {{ r.status }} ·
            受理={{ String(r.accepted ?? '-') }} 生效={{ String(r.effective ?? '-') }}
          </li>
        </ul>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="history.show = false">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import {
  continueAppeal,
  fetchEligibility,
  fetchSegmentHistory,
  fetchWorkbench,
  listEntries,
  locateAppeal,
  postWorkbenchAction,
  writeOffAppeal,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

type AnyRow = EntryRow & Record<string, any>

const versions = ref<AnyRow[]>([])
const version = ref('v2')
const reviewer = ref('reviewer_jia')
const bench = ref<Awaited<ReturnType<typeof fetchWorkbench>> | null>(null)
const eligibility = ref<Record<string, any> | null>(null)
const appeals = ref<AnyRow[]>([])
const errorMessage = ref('')
const busy = ref(false)
const tab = ref<'all' | '缺审' | '复核中' | '已拒绝' | '已通过'>('all')
const sortAsc = ref(true)
const showHistorical = ref(false)
const locatedId = ref<string>('')
const location = ref<Record<string, any> | null>(null)
const banner = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)
const tickets = reactive<Record<string, string>>({})
const writeoffIds = reactive<Record<string, string>>({})
const appealLocations = reactive<Record<string, Record<string, any>>>({})

const original = reactive({ claimId: '', conclusion: '通过', legacy: 'auto' })
const appealForm = reactive({ segment: 'SPCH-0004', position_ms: '13200', reason: '' })
const reseg = reactive({
  show: false, segmentId: '', splitAt: '', start_ms: '', end_ms: '', corrected_text: '',
})
const history = reactive<{ show: boolean; segmentId: string; data: Record<string, any> | null }>({
  show: false, segmentId: '', data: null,
})

const visibleItems = computed<AnyRow[]>(() => {
  let items = ((bench.value?.items as AnyRow[]) ?? []).slice()
  if (!showHistorical.value) items = items.filter((i) => i.current)
  if (tab.value !== 'all') items = items.filter((i) => i.category === tab.value)
  items.sort((a, b) => {
    const delta = Number(a.start_ms ?? 0) - Number(b.start_ms ?? 0)
    return sortAsc.value ? delta : -delta
  })
  return items
})

function categoryClass(category: string): string {
  if (category === '已通过') return 'pass'
  if (category === '已拒绝') return 'reject'
  if (category === '复核中') return 'doing'
  return 'missing'
}

function appealClass(status: string): string {
  if (status === '已核销') return 'pass'
  if (status === '续办中') return 'reject'
  if (status === '核查中') return 'doing'
  return 'missing'
}

function ticketFor(row: AnyRow): string {
  return tickets[String(row.segment_id)] || ''
}

function notify(ok: boolean, text: string): void {
  banner.value = { kind: ok ? 'ok' : 'err', text }
  if (!ok) errorMessage.value = text
  else errorMessage.value = ''
}

async function loadVersions(): Promise<void> {
  try {
    const page = await listEntries('transcript_version', { size: '200' })
    versions.value = page.items as AnyRow[]
    if (!versions.value.some((v) => v['版本号'] === version.value) && versions.value[0]) {
      version.value = String(versions.value[0]['版本号'])
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '版本列表读取失败'
  }
}

async function reload(): Promise<void> {
  errorMessage.value = ''
  try {
    const [workbench, appealPage] = await Promise.all([
      fetchWorkbench(version.value),
      listEntries('dictation_appeal', { size: '200' }),
    ])
    bench.value = workbench
    appeals.value = appealPage.items as AnyRow[]
    eligibility.value = await fetchEligibility(version.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '工作台读取失败'
  }
}

function toggleSort(): void {
  sortAsc.value = !sortAsc.value
}

function locate(row: AnyRow): void {
  locatedId.value = String(row.segment_id)
  location.value = (row.located as Record<string, any>) ?? {
    segment_id: row.segment_id,
    position_ms: row.start_ms,
    start_ms: row.start_ms,
    end_ms: row.end_ms,
    time_label: row.time_label,
  }
  document.getElementById(`seg-${row.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

async function claim(row: AnyRow): Promise<void> {
  busy.value = true
  try {
    const result = await postWorkbenchAction('claim', {
      version: version.value,
      segment_id: row.segment_id,
      reviewer: reviewer.value || 'anonymous',
    })
    const entry = result.entry as Record<string, any> | null
    if (result.ok && entry) {
      tickets[String(row.segment_id)] = String(entry['补审单号'])
      notify(true, result.message)
    } else {
      notify(false, result.message)
    }
    await reload()
  } finally {
    busy.value = false
  }
}

async function quickSubmit(row: AnyRow, conclusion: string): Promise<void> {
  const claimId = ticketFor(row) || String(row.claim_id || '')
  if (!claimId || claimId === 'None') {
    notify(false, '请先领取该段，或使用“凭原补审单交签”输入单号')
    return
  }
  await submitTicket(claimId, conclusion, 'auto')
}

async function submitOriginal(): Promise<void> {
  if (!original.claimId.trim()) {
    notify(false, '请输入补审单号')
    return
  }
  await submitTicket(original.claimId.trim(), original.conclusion, original.legacy)
}

async function submitTicket(claimId: string, conclusion: string, legacy: string): Promise<void> {
  busy.value = true
  try {
    const body: Record<string, unknown> = {
      claim_id: claimId,
      conclusion,
      reviewer: reviewer.value || undefined,
    }
    if (legacy === 'accept') body.accept_legacy = true
    if (legacy === 'reject') body.accept_legacy = false
    const result = await postWorkbenchAction('submit', body)
    notify(Boolean(result.ok), result.message)
    const entry = result.entry as Record<string, any> | null
    if (entry?.relisten) location.value = entry.relisten as Record<string, any>
    await reload()
  } finally {
    busy.value = false
  }
}

async function verifyRelease(): Promise<void> {
  eligibility.value = await fetchEligibility(version.value)
  notify(Boolean(eligibility.value.eligible), String(eligibility.value.message))
}

async function locateAppealRow(appeal: AnyRow): Promise<void> {
  const ref = String(appeal['申诉编号'])
  const loc = await locateAppeal(ref)
  appealLocations[ref] = loc
  location.value = loc
}

async function writeOff(appeal: AnyRow): Promise<void> {
  const ref = String(appeal['申诉编号'])
  const reviewId = writeoffIds[ref]
  const result = await writeOffAppeal(ref, reviewId ? { review_id: reviewId } : {})
  notify(Boolean(result.ok), result.message)
  if (result.entry) {
    const entry = result.entry as Record<string, any>
    if (entry.relisten) appealLocations[ref] = entry.relisten as Record<string, any>
  }
  await reload()
}

async function continueRow(appeal: AnyRow): Promise<void> {
  const ref = String(appeal['申诉编号'])
  const result = await continueAppeal(ref)
  notify(Boolean(result.ok), result.message)
  const entry = result.entry as Record<string, any> | null
  if (entry?.relisten) {
    appealLocations[ref] = entry.relisten as Record<string, any>
    location.value = entry.relisten as Record<string, any>
  }
  await reload()
}

async function createAppeal(): Promise<void> {
  const response = await request('/api/dictation_appeal/create', {
    method: 'POST',
    body: JSON.stringify({
      segment: appealForm.segment,
      position_ms: appealForm.position_ms ? Number(appealForm.position_ms) : undefined,
      reason: appealForm.reason,
      applicant: reviewer.value || 'editor_01',
    }),
  })
  const data = await response.json()
  notify(Boolean(data.ok), String(data.message ?? '申诉已登记'))
  appealForm.reason = ''
  await reload()
}

function openReseg(row: AnyRow): void {
  reseg.show = true
  reseg.segmentId = String(row.segment_id)
  reseg.splitAt = ''
  reseg.start_ms = ''
  reseg.end_ms = ''
  reseg.corrected_text = ''
}

async function submitReseg(): Promise<void> {
  const body: Record<string, unknown> = { segment_id: reseg.segmentId }
  if (reseg.splitAt.trim()) {
    body.split_at = reseg.splitAt.split(',').map((x) => Number(x.trim())).filter((n) => !Number.isNaN(n))
  }
  if (reseg.start_ms) body.start_ms = Number(reseg.start_ms)
  if (reseg.end_ms) body.end_ms = Number(reseg.end_ms)
  if (reseg.corrected_text) body.corrected_text = reseg.corrected_text
  const result = await postWorkbenchAction('resegment', body)
  notify(Boolean(result.ok), result.message)
  reseg.show = false
  showHistorical.value = true
  await reload()
}

async function showHistory(row: AnyRow): Promise<void> {
  history.show = true
  history.segmentId = String(row.segment_id)
  history.data = await fetchSegmentHistory(history.segmentId)
}

onMounted(async () => {
  await loadVersions()
  await reload()
})
</script>

<style scoped>
.filter-item select {
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  min-width: 220px;
}
.inline-check { flex-direction: row !important; align-items: center; gap: 6px; }
.inline-check input { width: auto !important; }
.bucket { cursor: pointer; }
.bucket.active { outline: 2px solid var(--brand); }
.bucket.reject .stat-value { color: #b42318; }
.bucket.pass .stat-value { color: #067647; }
.banner {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-radius: 8px; margin-bottom: 10px; font-size: 13px;
}
.banner.ok { background: #ecfdf3; color: #067647; border: 1px solid #abefc6; }
.banner.err { background: #fef3f2; color: #b42318; border: 1px solid #fecdca; }
.original-form {
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
  background: #fff; border: 1px dashed var(--brand); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px;
}
.original-form input, .original-form select {
  padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px;
}
.todo-table .time-cell { white-space: nowrap; }
.ms { color: var(--muted); font-size: 11px; }
.text-cell { max-width: 320px; }
td.low { color: #b54708; font-weight: 600; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 12px; background: #eef2f6; color: #475467; white-space: nowrap;
}
.badge.missing { background: #fffaeb; color: #b54708; }
.badge.doing { background: #eff8ff; color: #175cd3; }
.badge.pass { background: #ecfdf3; color: #067647; }
.badge.reject { background: #fef3f2; color: #b42318; }
.badge.hist { background: #f2f4f7; color: #667085; }
.badge.must { background: #f4f3ff; color: #5925dc; }
.badge.big { font-size: 14px; padding: 4px 12px; }
tr.located { background: #fffaeb; outline: 2px solid #f79009; }
tr.historical td { opacity: 0.72; }
.row-actions.wrap { flex-wrap: wrap; row-gap: 4px; }
.link.pass { color: #067647; }
.link.reject { color: #b42318; }
.location-card {
  margin: 10px 0; padding: 10px 12px; background: #eff8ff;
  border: 1px solid #b2ddff; border-radius: 8px; font-size: 13px;
}
.release-card, .appeal-card {
  background: #fff; border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 16px; margin: 18px 0;
}
.release-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.release-head h3 { margin: 0; font-size: 15px; }
.blockers { margin-top: 10px; color: #b42318; font-size: 13px; }
.blockers ul { margin: 6px 0 0 18px; }
.mini-input { width: 130px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 6px; }
.loc { color: #175cd3; font-size: 12px; margin-top: 2px; }
.filter-item.grow { flex: 1; }
.filter-item.grow input { width: 100%; }
.modal h4 { margin: 12px 0 6px; font-size: 14px; }
.modal ul { margin: 0; padding-left: 18px; font-size: 13px; }
</style>
