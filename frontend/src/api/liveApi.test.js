import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import {
  API_BASE_URL,
  ApiRequestError,
  NoValidSchedulesError,
  generateSchedules,
  getApiStatus,
  getCourseDetail,
  searchCourses,
} from './liveApi.js'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
})

const response = (payload, status = 200, requestId = 'req_test') => ({
  ok: status >= 200 && status < 300,
  status,
  headers: {
    get: (name) => name.toLowerCase() === 'x-request-id' ? requestId : null,
  },
  json: async () => payload,
})

const preferences = {
  days: ['M', 'W', 'F'],
  start: 540,
  end: 1020,
  gap: 60,
  weights: { days: 2, time: 3, gap: 1 },
}

test('uses the website API path by default', () => {
  assert.equal(API_BASE_URL, '/api/v1')
})

test('returns no search results without fetching for a short query', async () => {
  let called = false
  globalThis.fetch = async () => {
    called = true
    return response({ items: [] })
  }

  assert.deepEqual(await searchCourses(' a '), [])
  assert.equal(called, false)
})

test('maps course summaries and sends search options', async () => {
  const controller = new AbortController()
  let requestUrl
  let requestOptions
  globalThis.fetch = async (url, options) => {
    requestUrl = url
    requestOptions = options
    return response({
      items: [{
        code: 'CMPSC 16',
        name: 'PROBLEM SOLVING I',
        term: '2027-winter',
        units: 4,
        department: 'CMPSC',
        section_count: 12,
      }],
    })
  }

  const courses = await searchCourses('  CMPSC  ', { signal: controller.signal, limit: 7 })

  assert.deepEqual(courses, [{
    code: 'CMPSC 16',
    title: 'PROBLEM SOLVING I',
    units: 4,
    department: 'CMPSC',
    sectionCount: 12,
    term: '2027-winter',
  }])
  assert.equal(requestUrl, `${API_BASE_URL}/courses?q=CMPSC&limit=7`)
  assert.equal(requestOptions.signal, controller.signal)
})

test('gets API status and URL-encodes a course code', async () => {
  const urls = []
  globalThis.fetch = async (url) => {
    urls.push(url)
    return response(url.endsWith('/status') ? { status: 'ok' } : { code: 'CMPSC 16' })
  }

  assert.deepEqual(await getApiStatus(), { status: 'ok' })
  assert.deepEqual(await getCourseDetail(' CMPSC 16 '), { code: 'CMPSC 16' })
  assert.deepEqual(urls, [
    `${API_BASE_URL}/status`,
    `${API_BASE_URL}/courses/CMPSC%2016`,
  ])
})

test('maps generation input and ranked schedule output', async () => {
  let requestOptions
  globalThis.fetch = async (_url, options) => {
    requestOptions = options
    return response({
      request_id: 'sched_123',
      raw_schedule_count: 96,
      valid_schedule_count: 80,
      returned_schedule_count: 1,
      schedules: [{
        id: 'sched_123:1',
        score: 94.4444,
        score_breakdown: {
          days: { ratio: 1 },
          time_range: { ratio: 0.833333 },
          gaps: { ratio: 0.75 },
        },
        summary: {
          class_days: 4,
          earliest_start: 540,
          latest_end: 1005,
          minimum_gap: 30,
          outside_time_range_count: 2,
          non_preferred_day_count: 1,
          tba_meeting_count: 0,
        },
        meetings: [{
          id: 'meeting-1',
          meeting_type: 'lecture',
          days: ['M', 'W'],
          start_time: 930,
          end_time: 1005,
          location: { building: 'ILP', room: '1203', label: 'ILP 1203' },
          instructors: ['MAJEDI M'],
          course_code: 'CMPSC 16',
          course_name: 'PROBLEM SOLVING I',
          enroll_code: '08128',
          is_tba: false,
        }],
      }],
    })
  }

  const result = await generateSchedules(
    [{ code: 'CMPSC 16' }, { code: 'MATH 4A' }],
    preferences,
    { resultLimit: 1 },
  )
  const body = JSON.parse(requestOptions.body)

  assert.deepEqual(body, {
    course_codes: ['CMPSC 16', 'MATH 4A'],
    preferences: {
      preferred_days: ['M', 'W', 'F'],
      preferred_start: 540,
      preferred_end: 1020,
      minimum_gap: 60,
      weights: { days: 2, time_range: 3, gaps: 1 },
    },
    result_limit: 1,
  })
  assert.equal(requestOptions.method, 'POST')
  assert.equal(requestOptions.headers['Content-Type'], 'application/json')
  assert.deepEqual(result.meta, {
    rawCount: 96,
    validCount: 80,
    returnedCount: 1,
    requestId: 'sched_123',
  })
  assert.deepEqual(result.schedules[0], {
    id: 'sched_123:1',
    score: 94.4,
    breakdown: { days: 100, time: 83, gaps: 75 },
    stats: { activeDays: 4, earliest: 540, latest: 1005, shortestGap: 30, tbaCount: 0 },
    violations: [
      '1 meeting time falls outside your preferred days',
      '2 meeting times fall outside your preferred hours',
      'The shortest gap is 30 minutes, below your 60-minute minimum',
    ],
    explanation: 'This route scores best on preferred days, with 3 preference tradeoffs to review.',
    meetings: [{
      id: 'meeting-1',
      type: 'Lecture',
      days: ['M', 'W'],
      start: 930,
      end: 1005,
      location: 'ILP 1203',
      instructor: 'MAJEDI M',
      courseCode: 'CMPSC 16',
      courseTitle: 'PROBLEM SOLVING I',
      enrollment: '08128',
      isTba: false,
    }],
  })
})

test('preserves TBA meetings and safely maps null fields', async () => {
  globalThis.fetch = async () => response({
    request_id: 'sched_tba',
    raw_schedule_count: 1,
    valid_schedule_count: 1,
    returned_schedule_count: 1,
    schedules: [{
      id: 'sched_tba:1',
      score: 100,
      score_breakdown: {
        days: { ratio: 1 },
        time_range: { ratio: 1 },
        gaps: { ratio: 1 },
      },
      summary: {
        class_days: 0,
        earliest_start: null,
        latest_end: null,
        minimum_gap: 0,
        outside_time_range_count: 0,
        non_preferred_day_count: 0,
        tba_meeting_count: 1,
      },
      meetings: [{
        id: 'meeting-tba',
        meeting_type: null,
        days: null,
        start_time: null,
        end_time: null,
        location: null,
        instructors: null,
        course_code: 'ART 1A',
        course_name: 'STUDIO ART',
        enroll_code: null,
        is_tba: true,
      }],
    }],
  })

  const result = await generateSchedules(['ART 1A', 'ART 1B'], preferences)
  const schedule = result.schedules[0]

  assert.deepEqual(schedule.stats, {
    activeDays: 0,
    earliest: null,
    latest: null,
    shortestGap: 0,
    tbaCount: 1,
  })
  assert.deepEqual(schedule.meetings[0], {
    id: 'meeting-tba',
    type: 'Meeting',
    days: [],
    start: null,
    end: null,
    location: 'TBA',
    instructor: 'TBA',
    courseCode: 'ART 1A',
    courseTitle: 'STUDIO ART',
    enrollment: null,
    isTba: true,
  })
})

test('passes AbortError through unchanged', async () => {
  const abortError = new Error('aborted')
  abortError.name = 'AbortError'
  globalThis.fetch = async () => { throw abortError }

  await assert.rejects(searchCourses('math'), (error) => error === abortError)
})

test('maps the Phase 4 error envelope', async () => {
  globalThis.fetch = async () => response({
    error: {
      code: 'INVALID_PREFERENCES',
      message: 'The schedule preferences are invalid.',
      field_errors: { 'preferences.preferred_end': 'Must be greater than preferred_start.' },
      request_id: 'req_error',
      retryable: false,
    },
  }, 422)

  await assert.rejects(
    generateSchedules(['CMPSC 16', 'MATH 4A'], preferences),
    (error) => {
      assert.ok(error instanceof ApiRequestError)
      assert.equal(error.code, 'INVALID_PREFERENCES')
      assert.equal(error.requestId, 'req_error')
      assert.deepEqual(error.fieldErrors, { 'preferences.preferred_end': 'Must be greater than preferred_start.' })
      assert.equal(error.retryable, false)
      assert.equal(error.status, 422)
      return true
    },
  )
})

test('maps network failures to API_UNAVAILABLE', async () => {
  globalThis.fetch = async () => { throw new TypeError('fetch failed') }

  await assert.rejects(getApiStatus(), (error) => {
    assert.ok(error instanceof ApiRequestError)
    assert.equal(error.code, 'API_UNAVAILABLE')
    assert.equal(error.status, 0)
    assert.equal(error.retryable, true)
    return true
  })
})

test('throws NoValidSchedulesError when the API returns zero valid schedules', async () => {
  globalThis.fetch = async () => response({
    request_id: 'sched_none',
    raw_schedule_count: 12,
    valid_schedule_count: 0,
    returned_schedule_count: 0,
    schedules: [],
  })

  await assert.rejects(
    generateSchedules(['CMPSC 16', 'MATH 4A'], preferences),
    (error) => {
      assert.ok(error instanceof NoValidSchedulesError)
      assert.equal(error.code, 'NO_VALID_SCHEDULES')
      assert.deepEqual(error.meta, {
        rawCount: 12,
        validCount: 0,
        returnedCount: 0,
        requestId: 'sched_none',
      })
      return true
    },
  )
})
