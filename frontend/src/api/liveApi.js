const DEFAULT_API_BASE_URL = '/api/v1'

export const API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/+$/, '')

export class ApiRequestError extends Error {
  constructor(message, { code = 'API_REQUEST_FAILED', requestId = null, fieldErrors = {}, retryable = false, status = 0 } = {}) {
    super(message)
    this.name = 'ApiRequestError'
    this.code = code
    this.requestId = requestId
    this.fieldErrors = fieldErrors
    this.retryable = retryable
    this.status = status
  }
}

export class NoValidSchedulesError extends Error {
  constructor(meta) {
    const count = meta.rawCount
    const noun = count === 1 ? 'combination' : 'combinations'
    super(`We compared ${count} course ${noun}, but none produced a conflict-free schedule.`)
    this.name = 'NoValidSchedulesError'
    this.code = 'NO_VALID_SCHEDULES'
    this.meta = meta
  }
}

const requestIdFrom = (response, payload) =>
  payload?.error?.request_id ?? response.headers?.get?.('X-Request-ID') ?? null

const readPayload = async (response) => {
  try {
    return await response.json()
  } catch {
    return null
  }
}

const request = async (path, { method = 'GET', body, signal } = {}) => {
  let response

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new ApiRequestError('The scheduler API is unavailable. Check that the backend is running and try again.', {
      code: 'API_UNAVAILABLE',
      retryable: true,
      status: 0,
    })
  }

  const payload = await readPayload(response)

  if (!response.ok) {
    const envelope = payload?.error ?? {}
    throw new ApiRequestError(envelope.message || `The scheduler API returned HTTP ${response.status}.`, {
      code: envelope.code || 'API_REQUEST_FAILED',
      requestId: requestIdFrom(response, payload),
      fieldErrors: envelope.field_errors ?? {},
      retryable: envelope.retryable ?? response.status >= 500,
      status: response.status,
    })
  }

  if (payload === null) {
    throw new ApiRequestError('The scheduler API returned an unreadable response.', {
      code: 'INVALID_API_RESPONSE',
      requestId: requestIdFrom(response, payload),
      retryable: true,
      status: response.status,
    })
  }

  return payload
}

const percentage = (ratio) => Math.round((Number(ratio) || 0) * 100)

const roundedScore = (score) => Math.round((Number(score) || 0) * 10) / 10

const displayType = (type) => {
  if (!type) return 'Meeting'
  return type.charAt(0).toUpperCase() + type.slice(1).toLowerCase()
}

const displayLocation = (location) => {
  if (!location) return 'TBA'
  if (location.label) return location.label
  return [location.building, location.room].filter(Boolean).join(' ') || 'TBA'
}

const displayInstructor = (instructors) => {
  if (!Array.isArray(instructors) || instructors.length === 0) return 'TBA'
  return instructors.filter(Boolean).join(', ') || 'TBA'
}

const adaptMeeting = (meeting) => ({
  id: meeting.id,
  type: displayType(meeting.meeting_type),
  days: Array.isArray(meeting.days) ? meeting.days : [],
  start: Number.isInteger(meeting.start_time) ? meeting.start_time : null,
  end: Number.isInteger(meeting.end_time) ? meeting.end_time : null,
  location: displayLocation(meeting.location),
  instructor: displayInstructor(meeting.instructors),
  courseCode: meeting.course_code,
  courseTitle: meeting.course_name,
  enrollment: meeting.enroll_code ?? null,
  isTba: Boolean(meeting.is_tba),
})

const violationMessages = (summary, preferences) => {
  const violations = []
  const outsideDays = Number(summary.non_preferred_day_count) || 0
  const outsideTimes = Number(summary.outside_time_range_count) || 0
  const shortestGap = Number(summary.minimum_gap) || 0

  if (outsideDays > 0) {
    violations.push(`${outsideDays} meeting ${outsideDays === 1 ? 'time falls' : 'times fall'} outside your preferred days`)
  }
  if (outsideTimes > 0) {
    violations.push(`${outsideTimes} meeting ${outsideTimes === 1 ? 'time falls' : 'times fall'} outside your preferred hours`)
  }
  if (shortestGap > 0 && shortestGap < preferences.gap) {
    violations.push(`The shortest gap is ${shortestGap} minutes, below your ${preferences.gap}-minute minimum`)
  }

  return violations
}

const explanationFor = (breakdown, violations) => {
  const labels = {
    days: 'preferred days',
    time: 'time range',
    gaps: 'spacing between classes',
  }
  const strongest = Object.entries(breakdown).sort((left, right) => right[1] - left[1])[0]?.[0] ?? 'days'

  if (violations.length > 0) {
    return `This route scores best on ${labels[strongest]}, with ${violations.length} preference ${violations.length === 1 ? 'tradeoff' : 'tradeoffs'} to review.`
  }
  return `This route meets every selected preference and performs strongest on ${labels[strongest]}.`
}

const adaptSchedule = (schedule, preferences) => {
  const summary = schedule.summary ?? {}
  const breakdown = {
    days: percentage(schedule.score_breakdown?.days?.ratio),
    time: percentage(schedule.score_breakdown?.time_range?.ratio),
    gaps: percentage(schedule.score_breakdown?.gaps?.ratio),
  }
  const violations = violationMessages(summary, preferences)

  return {
    id: schedule.id,
    score: roundedScore(schedule.score),
    breakdown,
    stats: {
      activeDays: Number(summary.class_days) || 0,
      earliest: Number.isInteger(summary.earliest_start) ? summary.earliest_start : null,
      latest: Number.isInteger(summary.latest_end) ? summary.latest_end : null,
      shortestGap: Number(summary.minimum_gap) || 0,
      tbaCount: Number(summary.tba_meeting_count) || 0,
    },
    violations,
    explanation: explanationFor(breakdown, violations),
    meetings: Array.isArray(schedule.meetings) ? schedule.meetings.map(adaptMeeting) : [],
  }
}

export const getApiStatus = ({ signal } = {}) => request('/status', { signal })

export const searchCourses = async (query, { signal, limit = 20 } = {}) => {
  const normalizedQuery = query.trim()
  if (normalizedQuery.length < 2) return []

  const parameters = new URLSearchParams({
    q: normalizedQuery,
    limit: String(limit),
  })
  const payload = await request(`/courses?${parameters}`, { signal })

  return (payload.items ?? []).map((course) => ({
    code: course.code,
    title: course.name,
    units: course.units,
    department: course.department,
    sectionCount: course.section_count,
    term: course.term,
  }))
}

export const getCourseDetail = (code, { signal } = {}) =>
  request(`/courses/${encodeURIComponent(code.trim())}`, { signal })

export const generateSchedules = async (courses, preferences, { signal, resultLimit = 3 } = {}) => {
  const body = {
    course_codes: courses.map((course) => typeof course === 'string' ? course : course.code),
    preferences: {
      preferred_days: preferences.days,
      preferred_start: preferences.start,
      preferred_end: preferences.end,
      minimum_gap: preferences.gap,
      weights: {
        days: preferences.weights.days,
        time_range: preferences.weights.time,
        gaps: preferences.weights.gap,
      },
    },
    result_limit: resultLimit,
  }
  const payload = await request('/schedules/generate', {
    method: 'POST',
    body,
    signal,
  })
  const meta = {
    rawCount: payload.raw_schedule_count ?? 0,
    validCount: payload.valid_schedule_count ?? 0,
    returnedCount: payload.returned_schedule_count ?? 0,
    requestId: payload.request_id ?? null,
  }

  if (meta.validCount === 0) throw new NoValidSchedulesError(meta)

  return {
    schedules: (payload.schedules ?? []).map((schedule) => adaptSchedule(schedule, preferences)),
    meta,
  }
}
