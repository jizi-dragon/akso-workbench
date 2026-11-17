/**
 * journal（运行日志）的判据。
 *
 * 这一层的失败方式都很安静：口令/令牌被写进日志、日志写满磁盘、坏行把整份导出弄坏、
 * 换天后昨天的记录还在今天的文件里——用户下载回来的文件就是错的，而我们不会收到任何报错。
 * 因此这里逐条钉住：打码、切天、轮转、保留期、坏行、导出头。
 */
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import {
  JOURNAL_MAX_FILES_PER_DAY,
  JOURNAL_MAX_STRING,
  clip,
  createJournal,
  exportJournal,
  journalDir,
  journalSummary,
  maskEmail,
  readJournalRecords,
  redactValue,
  scrubText,
} from '../src/journal.ts'

const freshHome = () => mkdtempSync(join(tmpdir(), 'akso-wb-journal-'))
const at = (year: number, month: number, day: number, hour = 10, minute = 0) =>
  new Date(year, month - 1, day, hour, minute, 0, 0).getTime()

test('打码：敏感 key 的字符串/对象变占位符，布尔与数字保留（它们本身是诊断信息）', () => {
  const safe = redactValue({
    password: 'p@ssw0rd',
    token: { value: 'abc' },
    authorization: 'Bearer abcdefghijklmn',
    hasPassword: true,
    tokenCount: 2,
    note: '正常备注',
  }) as Record<string, unknown>
  assert.equal(safe.password, '[redacted]')
  assert.equal(safe.token, '[redacted]')
  assert.equal(safe.authorization, '[redacted]')
  assert.equal(safe.hasPassword, true)
  assert.equal(safe.tokenCount, 2)
  assert.equal(safe.note, '正常备注')
})

test('打码：字符串里的 JWT 与 Bearer 被替换，长字符串截到上限', () => {
  const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U'
  assert.equal(scrubText(`token=${jwt}`), 'token=[redacted-jwt]')
  assert.equal(scrubText('Authorization: Bearer abcdefghijkl'), 'Authorization: Bearer [redacted]')
  assert.equal(clip('x'.repeat(JOURNAL_MAX_STRING + 10)).length, JOURNAL_MAX_STRING + 1)
})

test('打码：嵌套过深给 [deep]，数组只留前 30 项', () => {
  const deep = { a: { b: { c: { d: { e: { f: { g: 'hidden' } } } } } } }
  const safe = redactValue(deep) as { a: { b: { c: { d: { e: unknown } } } } }
  assert.equal(safe.a.b.c.d.e, '[deep]')
  assert.equal((redactValue(Array.from({ length: 40 }, (_, index) => index)) as number[]).length, 30)
})

test('邮箱打码保留域名：能看出是哪个环境，但认不出人', () => {
  assert.equal(maskEmail('qa.zhang@example.com'), 'q***@example.com')
  assert.equal(maskEmail(''), '')
  assert.equal(maskEmail('not-an-email'), 'n***')
})

test('写读闭环：事件数、最早/最新时间、目录都在 $DSH_HOME/akso-workbench/journal 下', () => {
  const home = freshHome()
  const first = at(2026, 11, 17, 9, 30)
  const journal = createJournal(home, { now: () => first })
  assert.equal(journal.dir, join(home, 'akso-workbench', 'journal'))
  journal.record('tool.invoke', { tool: 'akso_workbench_accounts', uid: 'AKW-TOOL-ACCOUNTS-V1' })
  journal.record('http', { method: 'GET', path: '/api/akso-workbench/journal/summary', status: 200, ms: 3 })

  const summary = journal.summary(30)
  assert.equal(summary.events, 2)
  assert.equal(summary.files.length, 1)
  assert.equal(summary.oldest, new Date(first).toISOString())
  assert.equal(summary.newest, new Date(first).toISOString())
  assert.ok(summary.bytes > 0)

  const records = readJournalRecords(journal.dir, 30, first)
  assert.deepEqual(records.map((record) => record.kind), ['tool.invoke', 'http'])
  assert.equal(records[0]?.tool, 'akso_workbench_accounts')
})

test('切天：本地日期不同就落不同文件，读的时候只取范围内', () => {
  const home = freshHome()
  const day1 = at(2026, 11, 16, 23, 50)
  const day2 = at(2026, 11, 17, 0, 10)
  const journal = createJournal(home, { now: () => day1 })
  journal.record('a', {})
  const other = createJournal(home, { now: () => day2 })
  other.record('b', {})

  const names = readdirSync(journal.dir).sort()
  assert.equal(names.length, 2, `应按天分文件：${names.join(', ')}`)

  assert.deepEqual(readJournalRecords(journal.dir, 1, day2).map((record) => record.kind), ['b'])
  assert.deepEqual(readJournalRecords(journal.dir, 2, day2).map((record) => record.kind), ['a', 'b'])
})

test('坏行跳过：手工改坏一行不影响同一文件里的其他记录', () => {
  const home = freshHome()
  const now = at(2026, 11, 17, 12, 0)
  const journal = createJournal(home, { now: () => now })
  journal.record('good.one', {})
  const file = join(journal.dir, '2026-11-17.jsonl')
  writeFileSync(file, `${readFileSync(file, 'utf8')}{ not json }\n{"kind":"half"}\n`, 'utf8')
  journal.record('good.two', {})

  const records = readJournalRecords(journal.dir, 1, now)
  assert.deepEqual(records.map((record) => record.kind), ['good.one', 'good.two'])
  assert.equal(journalSummary(journal.dir, 1, now).events, 2)
})

test('轮转：当天的文件写满 4 MB 后换到 .2.jsonl', () => {
  const home = freshHome()
  const now = at(2026, 11, 17, 15, 0)
  const journal = createJournal(home, { now: () => now })
  mkdirSync(journal.dir, { recursive: true })
  const base = join(journal.dir, '2026-11-17.jsonl')
  // 先造一个刚好写满的文件：不必真写 4 MB 的合法记录，只验证选文件的行为
  writeFileSync(base, 'x'.repeat(4 * 1024 * 1024), 'utf8')
  journal.record('after.rotate', {})
  const rotated = join(journal.dir, '2026-11-17.2.jsonl')
  assert.ok(existsSync(rotated), '写满后应创建 .2 文件')
  assert.ok(readFileSync(rotated, 'utf8').includes('after.rotate'))
  assert.ok(statSync(base).size >= 4 * 1024 * 1024)
  assert.ok(JOURNAL_MAX_FILES_PER_DAY >= 2)
  rmSync(home, { recursive: true, force: true })
})

test('保留期：超过 30 天的文件在下次写入时被清掉', () => {
  const home = freshHome()
  const now = at(2026, 11, 17, 10, 0)
  const journal = createJournal(home, { now: () => now })
  mkdirSync(journal.dir, { recursive: true })
  const oldDay = '2026-01-01'
  writeFileSync(join(journal.dir, `${oldDay}.jsonl`), `${JSON.stringify({ at: '2026-01-01T02:00:00.000Z', ts: 1, kind: 'old' })}\n`, 'utf8')
  journal.record('new', {})
  assert.equal(existsSync(join(journal.dir, `${oldDay}.jsonl`)), false)
})

test('导出：第一行是 journal.meta，按时间升序，文件名带天数', () => {
  const home = freshHome()
  const now = at(2026, 11, 17, 11, 0)
  const journal = createJournal(home, { now: () => now })
  journal.record('one', {})
  journal.record('two', {})

  const exported = journal.export(7, { app: 'akso-workbench', version: '0.1.0' })
  const lines = exported.text.trimEnd().split('\n')
  assert.equal(lines.length, 3)
  const header = JSON.parse(lines[0] ?? '') as Record<string, unknown>
  assert.equal(header.kind, 'journal.meta')
  assert.equal(header.format, 'akso-workbench-journal')
  assert.equal(header.events, 2)
  assert.equal(header.app, 'akso-workbench')
  assert.deepEqual(
    lines.slice(1).map((line) => (JSON.parse(line) as { kind: string }).kind),
    ['one', 'two'],
  )
  assert.match(exported.fileName, /^akso-workbench-journal-\d{8}-\d{4}-7d\.jsonl$/)
  assert.equal(exported.truncated, false)
  assert.equal(exported.dropped, 0)
})

test('导出：空目录也给出可解析的 meta，而不是空文件', () => {
  const home = freshHome()
  const now = at(2026, 11, 17, 11, 0)
  const exported = exportJournal(journalDir(home), 7, now, { app: 'akso-workbench' })
  const first = JSON.parse(exported.text.split('\n')[0] ?? '') as Record<string, unknown>
  assert.equal(first.kind, 'journal.meta')
  assert.equal(first.events, 0)
  assert.equal(exported.events, 0)
})

test('路径来自 dshHome，不在仓库或当前目录写日志', () => {
  const home = freshHome()
  const journal = createJournal(home, { now: () => at(2026, 11, 17, 9, 0) })
  journal.record('probe', {})
  assert.ok(journal.dir.startsWith(home))
  assert.ok(existsSync(join(journal.dir, '2026-11-17.jsonl')))
})
