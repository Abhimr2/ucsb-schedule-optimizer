import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  Check,
  Clock3,
  Database,
  Info,
  MapPin,
  PanelTopOpen,
  Plus,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
  X,
} from 'lucide-react'
import {
  ApiRequestError,
  NoValidSchedulesError,
  generateSchedules,
  getApiStatus,
  searchCourses,
} from './api/liveApi.js'

const DAYS = [
  ['M', 'Mon'],
  ['T', 'Tue'],
  ['W', 'Wed'],
  ['R', 'Thu'],
  ['F', 'Fri'],
]
const COURSE_COLORS = ['aqua', 'coral', 'green', 'gold', 'blue']
const START_HOUR = 8
const END_HOUR = 20
const DEFAULT_PREFERENCES = {
  days: ['M', 'W', 'F'],
  start: 540,
  end: 1020,
  gap: 15,
  weights: { days: 2, time: 3, gap: 1 },
}

const createDefaultPreferences = () => ({
  ...DEFAULT_PREFERENCES,
  days: [...DEFAULT_PREFERENCES.days],
  weights: { ...DEFAULT_PREFERENCES.weights },
})

const formatTime = (minutes) => {
  if (!Number.isFinite(minutes)) return 'TBA'
  const hour = Math.floor(minutes / 60)
  const minute = minutes % 60
  const displayHour = hour % 12 || 12
  return `${displayHour}:${String(minute).padStart(2, '0')} ${hour >= 12 ? 'PM' : 'AM'}`
}

const isAbortError = (error) => error?.name === 'AbortError'

const scrollToTop = () => {
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  window.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' })
}

const errorRequestId = (error) =>
  error?.requestId ?? error?.request_id ?? error?.meta?.requestId ?? error?.meta?.request_id ?? null

const errorMessage = (error, fallback) => {
  const message = error instanceof ApiRequestError || error instanceof NoValidSchedulesError
    ? error.message
    : error?.message || fallback
  const requestId = errorRequestId(error)
  return requestId ? `${message} Request ID: ${requestId}.` : message
}

const isTimedMeeting = (meeting) =>
  !meeting?.isTba && Number.isFinite(meeting?.start) && Number.isFinite(meeting?.end)

const meetingDays = (meeting) => Array.isArray(meeting?.days) ? meeting.days : []

const snapshotContext = (payload) => {
  const freshness = payload?.freshness ?? payload?.meta?.freshness ?? {}
  const updatedAt = freshness.last_updated_at ?? freshness.lastUpdatedAt
  const updatedDate = updatedAt ? new Date(updatedAt) : null
  const updatedLabel = updatedDate && !Number.isNaN(updatedDate.getTime())
    ? updatedDate.toLocaleString()
    : 'update time unavailable'
  return `Winter 2027 UCSB snapshot from the UCSB Academic Curriculums API. Last updated ${updatedLabel}.`
}

function AppHeader({ onReset, apiHealth, apiContext }) {
  const statusLabel = apiHealth === 'healthy' ? 'UCSB GOLD' : apiHealth === 'offline' ? 'API offline' : 'Connecting'
  return (
    <header className="app-header">
      <div className="brand-lockup">
        <span className="brand-mark" aria-hidden="true"><span /><span /><span /></span>
        <div>
          <p className="eyebrow">UCSB planning studio</p>
          <h1>UCSB Scheduler Optimizer</h1>
        </div>
      </div>
      <div className="header-actions">
        <div className="term-status">
          <span className={`data-status is-${apiHealth}`} title={apiContext} role="status" aria-live="polite" aria-label={`${statusLabel}. ${apiContext}`}>
            <Database size={15} aria-hidden="true" />
            <span className="data-label-full">{statusLabel}</span>
            <span className="data-label-compact" aria-hidden="true">{apiHealth === 'healthy' ? 'GOLD' : statusLabel}</span>
          </span>
          <span className="term-label">Winter 2027</span>
        </div>
        <button className="header-reset" type="button" onClick={onReset} title="Reset planner and start over">
          <RotateCcw size={17} />
          <span>Start over</span>
        </button>
      </div>
    </header>
  )
}

function WorkflowTabs({ activeView, onChange, selectedCount, resultCount, resultsAvailable }) {
  const tabRefs = useRef([])
  const tabs = [
    { id: 'courses', index: '01', label: 'Courses', detail: selectedCount ? `${selectedCount} selected` : 'Choose classes', icon: Search },
    { id: 'preferences', index: '02', label: 'Preferences', detail: 'Shape your week', icon: SlidersHorizontal },
    { id: 'results', index: '03', label: 'Results', detail: resultCount ? `${resultCount} ranked` : 'Generate first', icon: CalendarDays, disabled: !resultsAvailable },
  ]

  const handleKeyDown = (event, index) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const enabledIndexes = tabs.map((tab, tabIndex) => (!tab.disabled ? tabIndex : -1)).filter((tabIndex) => tabIndex >= 0)
    const currentEnabledIndex = enabledIndexes.indexOf(index)
    let nextIndex
    if (event.key === 'Home') nextIndex = enabledIndexes[0]
    else if (event.key === 'End') nextIndex = enabledIndexes.at(-1)
    else if (event.key === 'ArrowRight') nextIndex = enabledIndexes[(currentEnabledIndex + 1) % enabledIndexes.length]
    else nextIndex = enabledIndexes[(currentEnabledIndex - 1 + enabledIndexes.length) % enabledIndexes.length]
    onChange(tabs[nextIndex].id)
    tabRefs.current[nextIndex]?.focus()
  }

  return (
    <nav className="workflow-tabs" role="tablist" aria-label="Schedule builder steps">
      {tabs.map((tab, index) => {
        const Icon = tab.icon
        const isActive = activeView === tab.id
        return (
          <button
            ref={(element) => { tabRefs.current[index] = element }}
            key={tab.id}
            id={`workflow-tab-${tab.id}`}
            className={`workflow-tab ${isActive ? 'is-active' : ''}`}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-controls={`workflow-panel-${tab.id}`}
            tabIndex={isActive ? 0 : -1}
            disabled={tab.disabled}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            <span className="workflow-index">{tab.index}</span>
            <Icon size={19} aria-hidden="true" />
            <span className="workflow-copy"><strong>{tab.label}</strong><small>{tab.detail}</small></span>
          </button>
        )
      })}
    </nav>
  )
}

function CourseSearch({ selected, onSelect }) {
  const [query, setQuery] = useState('')
  const [courses, setCourses] = useState([])
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [activeIndex, setActiveIndex] = useState(-1)
  const requestRef = useRef(0)
  const abortRef = useRef(null)
  const comboboxRef = useRef(null)
  const [resultsMaxHeight, setResultsMaxHeight] = useState(320)

  useEffect(() => {
    const normalizedQuery = query.trim()
    const requestId = ++requestRef.current
    abortRef.current?.abort()

    if (normalizedQuery.length < 2) return undefined

    const timer = window.setTimeout(() => {
      const controller = new AbortController()
      abortRef.current = controller
      setStatus('loading')
      setError('')
      searchCourses(normalizedQuery, { signal: controller.signal })
        .then((results) => {
          if (requestId !== requestRef.current) return
          setCourses(results)
          setStatus('success')
          setActiveIndex(-1)
        })
        .catch((searchError) => {
          if (controller.signal.aborted || isAbortError(searchError) || requestId !== requestRef.current) return
          setCourses([])
          setError(errorMessage(searchError, 'Course search is unavailable. Try again.'))
          setStatus('error')
          setActiveIndex(-1)
        })
    }, 250)
    return () => {
      window.clearTimeout(timer)
      abortRef.current?.abort()
    }
  }, [query])

  useEffect(() => {
    if (!open) return undefined

    const updateResultsHeight = () => {
      const combobox = comboboxRef.current
      const deck = combobox?.closest('.selection-deck')
      const field = combobox?.querySelector('.search-field')
      if (!deck || !field) return

      const availableHeight = deck.getBoundingClientRect().bottom - field.getBoundingClientRect().bottom - 16
      setResultsMaxHeight(Math.max(120, Math.min(380, Math.floor(availableHeight))))
    }

    updateResultsHeight()
    window.addEventListener('resize', updateResultsHeight)
    const observer = new ResizeObserver(updateResultsHeight)
    const deck = comboboxRef.current?.closest('.selection-deck')
    if (deck) observer.observe(deck)

    return () => {
      window.removeEventListener('resize', updateResultsHeight)
      observer.disconnect()
    }
  }, [open])

  const addCourse = (course) => {
    onSelect(course)
    setQuery('')
    setCourses([])
    setStatus('idle')
    setError('')
    setActiveIndex(-1)
    setOpen(false)
  }

  const updateQuery = (nextQuery) => {
    setQuery(nextQuery)
    setOpen(true)
    if (nextQuery.trim().length >= 2) return
    setCourses([])
    setStatus('idle')
    setError('')
    setActiveIndex(-1)
  }

  const handleKeyDown = (event) => {
    const availableIndexes = courses
      .map((course, index) => {
        const unavailable = selected.length >= 5 || selected.some((item) => item.code === course.code)
        return unavailable ? -1 : index
      })
      .filter((index) => index >= 0)

    if (!open && event.key === 'ArrowDown') setOpen(true)
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((index) => {
        const position = availableIndexes.indexOf(index)
        return availableIndexes[Math.min(position + 1, availableIndexes.length - 1)] ?? -1
      })
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((index) => {
        const position = availableIndexes.indexOf(index)
        if (position < 0) return availableIndexes.at(-1) ?? -1
        return availableIndexes[Math.max(position - 1, 0)] ?? -1
      })
    }
    if (event.key === 'Enter' && activeIndex >= 0 && availableIndexes.includes(activeIndex)) {
      event.preventDefault()
      addCourse(courses[activeIndex])
    }
    if (event.key === 'Escape') {
      setOpen(false)
      setActiveIndex(-1)
    }
  }

  return (
    <div className="course-search">
      <label htmlFor="course-query">Find a course</label>
      <div className="search-combobox" ref={comboboxRef}>
        <div className="search-field">
          <Search size={19} aria-hidden="true" />
          <input
            id="course-query"
            value={query}
            onChange={(event) => updateQuery(event.target.value)}
            onFocus={() => setOpen(true)}
            onKeyDown={handleKeyDown}
            placeholder="Try CMPSC, MATH 4A, or astronomy"
            autoComplete="off"
            role="combobox"
            aria-expanded={open}
            aria-controls="course-results"
            aria-describedby={selected.length >= 5 ? 'course-limit-help' : undefined}
            aria-activedescendant={activeIndex >= 0 ? `course-result-${activeIndex}` : undefined}
          />
          {query && <button className="icon-button" onClick={() => updateQuery('')} aria-label="Clear search"><X size={17} /></button>}
        </div>
        {open && (
          <div className="search-results" id="course-results" style={{ '--search-results-max-height': `${resultsMaxHeight}px` }} role={status === 'success' && courses.length ? 'listbox' : 'status'} aria-busy={status === 'loading'}>
            {status === 'idle' && <div className="search-message">Type at least 2 characters to search the Winter 2027 UCSB snapshot.</div>}
            {status === 'loading' && <div className="search-message"><span className="spinner" /> Searching courses...</div>}
            {status === 'error' && <div className="search-message error">{error}</div>}
            {status === 'success' && courses.length === 0 && <div className="search-message">No courses match “{query}”.</div>}
            {status === 'success' && courses.map((course, index) => {
              const isSelected = selected.some((item) => item.code === course.code)
              const isFull = selected.length >= 5
              return (
                <button
                  id={`course-result-${index}`}
                  key={course.code}
                  className={`course-result ${index === activeIndex ? 'is-active' : ''}`}
                  role="option"
                  aria-selected={isSelected}
                  aria-disabled={isSelected || isFull}
                  tabIndex={-1}
                  disabled={isSelected || isFull}
                  onClick={() => addCourse(course)}
                >
                  <span><strong>{course.code}</strong><small>{course.title}</small></span>
                  <span className="result-meta">{isFull && !isSelected ? '5-course limit' : course.units == null ? 'Units TBA' : `${course.units} units`} {isSelected ? <Check size={17} /> : !isFull && <Plus size={17} />}</span>
                </button>
              )
            })}
          </div>
        )}
      </div>
      {selected.length >= 5 && <p className="course-limit-message" id="course-limit-help">Five-course maximum reached. Remove a ticket before adding another course.</p>}
    </div>
  )
}

function CourseRoute({ courses, onRemove }) {
  const removeButtonRefs = useRef([])
  const removeCourse = (code, index) => {
    onRemove(code)
    window.requestAnimationFrame(() => {
      const nextIndex = Math.min(index, courses.length - 2)
      if (nextIndex >= 0) removeButtonRefs.current[nextIndex]?.focus()
      else document.getElementById('course-query')?.focus()
    })
  }

  return (
    <section className="route-section" aria-labelledby="route-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Step 1</p>
          <h2 id="route-title">Build your course route</h2>
        </div>
        <span className="selection-count">{courses.length}<span>/5</span></span>
      </div>
      <div className={`ticket-route ${courses.length ? 'has-courses' : ''}`}>
        {courses.length === 0 ? (
          <div className="route-empty"><ArrowRight size={20} /> Search above to add your first course ticket.</div>
        ) : courses.map((course, index) => (
          <article className={`course-ticket color-${COURSE_COLORS[index]}`} key={course.code}>
            <span className="route-number">{String(index + 1).padStart(2, '0')}</span>
            <div><strong>{course.code}</strong><small>{course.title}</small></div>
            <button ref={(element) => { removeButtonRefs.current[index] = element }} onClick={() => removeCourse(course.code, index)} aria-label={`Remove ${course.code}`}><X size={17} /></button>
          </article>
        ))}
        {courses.length > 0 && courses.length < 5 && <span className="route-end" aria-hidden="true"><Plus size={16} /></span>}
      </div>
      {courses.length >= 5 && <p className="selection-limit">Your route is full at five courses. Remove one ticket to explore another course.</p>}
    </section>
  )
}

function SegmentedWeight({ label, value, onChange }) {
  return (
    <fieldset className="weight-control">
      <legend>{label}</legend>
      <div>
        {[1, 2, 3].map((weight) => (
          <button key={weight} type="button" className={value === weight ? 'is-selected' : ''} onClick={() => onChange(weight)} aria-pressed={value === weight}>
            {['Low', 'Med', 'High'][weight - 1]}
          </button>
        ))}
      </div>
    </fieldset>
  )
}

function Preferences({ preferences, onChange }) {
  const update = (key, value) => onChange({ ...preferences, [key]: value })
  const updateWeight = (key, value) => onChange({ ...preferences, weights: { ...preferences.weights, [key]: value } })
  const toggleDay = (day) => update('days', preferences.days.includes(day) ? preferences.days.filter((item) => item !== day) : [...preferences.days, day])

  return (
    <section className="preferences-editor" aria-labelledby="preferences-title">
      <div className="preferences-editor-heading">
        <span className="preference-icon"><SlidersHorizontal size={22} /></span>
        <div>
          <p className="eyebrow">Optimization controls</p>
          <h2 id="preferences-title">Shape your ideal week</h2>
        </div>
        <p>{preferences.days.join(' · ') || 'Any day'} <i /> {formatTime(preferences.start)}–{formatTime(preferences.end)}</p>
      </div>
      <div className="preference-controls" id="preference-controls">
        <fieldset className="day-control">
          <legend>Preferred days</legend>
          <div>{DAYS.map(([code, label]) => <button key={code} type="button" onClick={() => toggleDay(code)} aria-pressed={preferences.days.includes(code)} className={preferences.days.includes(code) ? 'is-selected' : ''}>{label}</button>)}</div>
        </fieldset>
        <label className="select-control">Earliest start
          <select value={preferences.start} onChange={(event) => update('start', Number(event.target.value))}>
            {[480, 540, 600, 660, 720].map((time) => <option key={time} value={time}>{formatTime(time)}</option>)}
          </select>
        </label>
        <label className="select-control">Latest end
          <select value={preferences.end} onChange={(event) => update('end', Number(event.target.value))}>
            {[900, 960, 1020, 1080, 1140, 1200].map((time) => <option key={time} value={time}>{formatTime(time)}</option>)}
          </select>
        </label>
        <label className="select-control">Minimum gap
          <select value={preferences.gap} onChange={(event) => update('gap', Number(event.target.value))}>
            {[15, 30, 45, 60].map((gap) => <option key={gap} value={gap}>{gap} minutes</option>)}
          </select>
        </label>
        <div className="weight-set" aria-label="Preference importance">
          <SegmentedWeight label="Days" value={preferences.weights.days} onChange={(value) => updateWeight('days', value)} />
          <SegmentedWeight label="Times" value={preferences.weights.time} onChange={(value) => updateWeight('time', value)} />
          <SegmentedWeight label="Gaps" value={preferences.weights.gap} onChange={(value) => updateWeight('gap', value)} />
        </div>
      </div>
    </section>
  )
}

function GenerateBand({ count, status, onGenerate, onBack, error }) {
  const ready = count >= 2
  return (
    <section className="generate-band" aria-live="polite">
      <div>
        <p className="eyebrow">Step 2</p>
        <strong>{ready ? `${count} courses ready to optimize` : 'Choose at least two courses'}</strong>
        <span>{ready ? 'We’ll compare meeting combinations against your preferences.' : 'Add another course to unlock schedule generation.'}</span>
      </div>
      <div className="generate-actions">
        <button className="secondary-action" type="button" onClick={onBack}><ArrowLeft size={18} /> Back to courses</button>
        <button className="generate-button" onClick={onGenerate} disabled={!ready || status === 'loading'}>
          {status === 'loading' ? <><span className="spinner dark" /> Optimizing...</> : <><Sparkles size={19} /> Generate schedules</>}
        </button>
      </div>
      {error && <p className="generation-error">{error}</p>}
    </section>
  )
}

function ResultNavigator({ results, activeIndex, onSelect }) {
  const tabRefs = useRef([])
  const handleKeyDown = (event, index) => {
    let nextIndex
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % results.length
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + results.length) % results.length
    else if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = results.length - 1
    else return
    event.preventDefault()
    onSelect(nextIndex)
    tabRefs.current[nextIndex]?.focus()
  }

  return (
    <div className="result-navigator" role="tablist" aria-label="Ranked schedules">
      {results.map((result, index) => (
        <button
          key={result.id}
          ref={(element) => { tabRefs.current[index] = element }}
          id={`schedule-tab-${index}`}
          role="tab"
          aria-selected={activeIndex === index}
          aria-controls="schedule-panel"
          tabIndex={activeIndex === index ? 0 : -1}
          className={activeIndex === index ? 'is-active' : ''}
          onClick={() => onSelect(index)}
          onKeyDown={(event) => handleKeyDown(event, index)}
        >
          <span>{index === 0 ? 'BEST' : `0${index + 1}`}</span>
          <strong>{result.score}%</strong>
          <small>match</small>
        </button>
      ))}
    </div>
  )
}

function ScoreSummary({ result, meta }) {
  const meetings = result.meetings ?? []
  const tbaCount = Number(result.stats?.tbaCount) || meetings.filter((meeting) => meeting?.isTba).length
  return (
    <div className="result-analysis">
      <section className="score-summary" aria-label="Schedule score breakdown">
        <div className="summary-score"><strong>{result.score}</strong><span>% match</span></div>
        {Object.entries(result.breakdown).map(([key, value]) => (
          <div className="score-metric" key={key}>
            <span>{key}</span>
            <div><i style={{ width: `${value}%` }} /></div>
            <strong>{value}%</strong>
          </div>
        ))}
        <div className="schedule-stat"><CalendarDays size={17} /><strong>{result.stats.activeDays ?? 0}</strong><span>class days</span></div>
        <div className="schedule-stat"><Clock3 size={17} /><strong>{formatTime(result.stats.latest)}</strong><span>latest end</span></div>
      </section>
      <section className="score-context" aria-label="Why this schedule scored this way">
        <div className="score-explanation"><Info size={18} /><p><strong>Why this score</strong>{result.explanation}</p></div>
        <dl className="route-facts">
          <div><dt>Earliest start</dt><dd>{formatTime(result.stats.earliest)}</dd></div>
          <div><dt>Shortest gap</dt><dd>{result.stats.shortestGap ? `${result.stats.shortestGap} min` : 'No gaps'}</dd></div>
          <div><dt>Combinations</dt><dd>{meta?.rawCount ?? 0} raw · {meta?.validCount ?? 0} valid</dd></div>
        </dl>
        <div className="violation-summary">
          <strong>{result.violations.length ? `${result.violations.length} preference ${result.violations.length === 1 ? 'tradeoff' : 'tradeoffs'}` : 'All preferences met'}</strong>
          <span>{result.violations.length ? result.violations.join(' · ') : 'No preferred-day, time-range, or gap violations.'}</span>
        </div>
        {tbaCount > 0 && (
          <div className="violation-summary tba-summary" role="status">
            <strong>{tbaCount} TBA {tbaCount === 1 ? 'meeting' : 'meetings'}</strong>
            <span>Untimed meetings are included in this result but are not placed on the calendar.</span>
          </div>
        )}
      </section>
    </div>
  )
}

function MeetingDetails({ meeting, color, onClose }) {
  const closeButtonRef = useRef(null)

  useEffect(() => {
    if (!meeting) return
    closeButtonRef.current?.focus()
  }, [meeting])

  if (!meeting) return null
  const days = meetingDays(meeting)
  const enrollment = meeting.enrollment ?? meeting.id ?? 'Unavailable'
  const handleKeyDown = (event) => {
    if (event.key === 'Escape') onClose()
    if (event.key === 'Tab') {
      event.preventDefault()
      closeButtonRef.current?.focus()
    }
  }
  return (
    <>
      <button className="meeting-backdrop" type="button" tabIndex={-1} aria-hidden="true" onClick={onClose} />
      <aside className={`meeting-details color-${color}`} role="dialog" aria-modal="true" aria-labelledby="meeting-details-title" onKeyDown={handleKeyDown}>
        <button ref={closeButtonRef} className="icon-button" onClick={onClose} aria-label="Close meeting details"><X size={18} /></button>
        <p className="eyebrow">{meeting.type ?? 'Meeting'} · Enrollment {enrollment}</p>
        <h3 id="meeting-details-title">{meeting.courseCode}</h3>
        <p>{meeting.courseTitle}</p>
        <dl>
          <div><dt><Clock3 size={16} />Time</dt><dd>{days.length ? days.join(' · ') : 'Days TBA'} · {formatTime(meeting.start)}–{formatTime(meeting.end)}</dd></div>
          <div><dt><MapPin size={16} />Location</dt><dd>{meeting.location || 'Location TBA'}</dd></div>
          <div><dt><Info size={16} />Instructor</dt><dd>{meeting.instructor || 'Instructor TBA'}</dd></div>
        </dl>
      </aside>
    </>
  )
}

function DesktopCalendar({ result, selectedCourses, selectedMeeting, preferredDays, onMeetingSelect }) {
  const timedMeetings = (result.meetings ?? []).filter(isTimedMeeting)
  const meetingStarts = timedMeetings.map((meeting) => meeting.start)
  const meetingEnds = timedMeetings.map((meeting) => meeting.end)
  const earliestMeeting = meetingStarts.length ? Math.min(...meetingStarts) : START_HOUR * 60
  const latestMeeting = meetingEnds.length ? Math.max(...meetingEnds) : END_HOUR * 60
  const visibleStartHour = Math.min(START_HOUR, Math.floor(earliestMeeting / 60))
  const visibleEndHour = Math.max(END_HOUR, Math.ceil(latestMeeting / 60))
  const visibleStart = visibleStartHour * 60
  const visibleDuration = (visibleEndHour - visibleStartHour) * 60
  const hourCount = visibleEndHour - visibleStartHour
  const hours = Array.from({ length: hourCount + 1 }, (_, index) => visibleStartHour + index)
  const colorFor = (code) => COURSE_COLORS[Math.max(0, selectedCourses.findIndex((course) => course.code === code)) % COURSE_COLORS.length]
  return (
    <div className="desktop-calendar" aria-label="Weekly schedule">
      <div className="calendar-corner">PST</div>
      {DAYS.map(([code, name]) => <div className={`day-heading ${preferredDays.includes(code) ? 'is-preferred' : ''}`} key={name}>{name}</div>)}
      <div className="time-axis">
        {hours.map((hour, index) => (
          <span
            className={`${index === 0 ? 'is-start' : ''} ${index === hours.length - 1 ? 'is-end' : ''}`}
            key={hour}
            style={{ top: `${((hour - visibleStartHour) / hourCount) * 100}%` }}
          >
            {formatTime(hour * 60).replace(':00', '')}
          </span>
        ))}
      </div>
      {DAYS.map(([code]) => (
        <div className="day-column" key={code}>
          {Array.from({ length: hourCount - 1 }, (_, index) => (
            <span className="hour-line" key={index} style={{ top: `${((index + 1) / hourCount) * 100}%` }} />
          ))}
          {timedMeetings.filter((item) => meetingDays(item).includes(code)).map((item) => {
            const top = ((item.start - visibleStart) / visibleDuration) * 100
            const height = ((item.end - item.start) / visibleDuration) * 100
            const color = colorFor(item.courseCode)
            return (
              <button
                key={`${item.courseCode}-${item.id}-${code}`}
                className={`calendar-block color-${color} meeting-${(item.type ?? 'meeting').toLowerCase().replace(/[^a-z0-9-]/g, '-')} ${selectedMeeting?.id === item.id ? 'is-selected' : ''}`}
                style={{ top: `${top}%`, height: `${height}%` }}
                onClick={() => onMeetingSelect(item)}
                aria-label={`${DAYS.find(([dayCode]) => dayCode === code)?.[1] ?? code}, ${item.courseCode} ${item.type ?? 'meeting'}, ${formatTime(item.start)} to ${formatTime(item.end)}, ${item.location || 'location TBA'}`}
              >
                <strong>{item.courseCode}</strong><span>{formatTime(item.start)}–{formatTime(item.end)}</span><small>{item.type ?? 'Meeting'}</small>
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function MobileAgenda({ result, selectedCourses, selectedMeeting, onMeetingSelect }) {
  const [activeDay, setActiveDay] = useState('M')
  const tabRefs = useRef([])
  const meetings = (result.meetings ?? [])
    .filter((item) => isTimedMeeting(item) && meetingDays(item).includes(activeDay))
    .sort((a, b) => a.start - b.start)
  const colorFor = (code) => COURSE_COLORS[Math.max(0, selectedCourses.findIndex((course) => course.code === code)) % COURSE_COLORS.length]
  const activeDayIndex = DAYS.findIndex(([code]) => code === activeDay)
  const selectDay = (index) => {
    setActiveDay(DAYS[index][0])
    tabRefs.current[index]?.focus()
  }
  const handleKeyDown = (event, index) => {
    let nextIndex
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % DAYS.length
    else if (event.key === 'ArrowLeft') nextIndex = (index - 1 + DAYS.length) % DAYS.length
    else if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = DAYS.length - 1
    else return
    event.preventDefault()
    selectDay(nextIndex)
  }
  return (
    <div className="mobile-agenda">
      <div className="agenda-days" role="tablist" aria-label="Schedule day">
        {DAYS.map(([code, name], index) => <button ref={(element) => { tabRefs.current[index] = element }} id={`day-tab-${code}`} key={code} role="tab" aria-selected={activeDay === code} aria-controls="agenda-panel" tabIndex={activeDay === code ? 0 : -1} className={activeDay === code ? 'is-active' : ''} onClick={() => selectDay(index)} onKeyDown={(event) => handleKeyDown(event, index)}>{name}</button>)}
      </div>
      <div className="agenda-list" id="agenda-panel" role="tabpanel" aria-labelledby={`day-tab-${DAYS[activeDayIndex][0]}`} tabIndex="0">
        {meetings.length === 0 ? <div className="agenda-empty">No classes. This day is yours.</div> : meetings.map((item) => (
          <button key={`${item.courseCode}-${item.id}`} className={`agenda-item color-${colorFor(item.courseCode)} ${selectedMeeting?.id === item.id ? 'is-selected' : ''}`} onClick={() => onMeetingSelect(item)}>
            <time>{formatTime(item.start)}<span>{formatTime(item.end)}</span></time>
            <span><strong>{item.courseCode}</strong><small>{item.type ?? 'Meeting'} · {item.location || 'Location TBA'}</small></span>
            <ArrowRight size={18} />
          </button>
        ))}
      </div>
    </div>
  )
}

function EmptyWorkspace() {
  return (
    <div className="workspace-empty">
      <span className="empty-symbol"><PanelTopOpen size={30} /></span>
      <p className="eyebrow">Your week, before the rush</p>
      <h2>Build a route worth waking up for.</h2>
      <p>Add two or more courses, tune what matters, and compare your three strongest schedules here.</p>
    </div>
  )
}

function NoValidWorkspace({ message, meta }) {
  return (
    <div className="workspace-empty no-valid-state" role="status">
      <span className="empty-symbol"><X size={30} /></span>
      <p className="eyebrow">No conflict-free route</p>
      <h2>These courses cannot fit together yet.</h2>
      <p>{message} Try removing a course or choosing a different set before generating again.</p>
      {meta && <p className="combination-audit">{meta.rawCount ?? 0} raw combinations · {meta.validCount ?? 0} valid</p>}
    </div>
  )
}

export default function App() {
  const [selectedCourses, setSelectedCourses] = useState([])
  const [preferences, setPreferences] = useState(createDefaultPreferences)
  const [plannerVersion, setPlannerVersion] = useState(0)
  const [activeView, setActiveView] = useState('courses')
  const [apiHealth, setApiHealth] = useState('checking')
  const [apiContext, setApiContext] = useState('Checking the Winter 2027 UCSB snapshot connection.')
  const [status, setStatus] = useState('idle')
  const [results, setResults] = useState([])
  const [activeResult, setActiveResult] = useState(0)
  const [selectedMeeting, setSelectedMeeting] = useState(null)
  const [error, setError] = useState('')
  const [generationMeta, setGenerationMeta] = useState(null)
  const [announcement, setAnnouncement] = useState('')
  const generationRequestRef = useRef(0)
  const generationAbortRef = useRef(null)
  const coursesPanelRef = useRef(null)
  const preferencesPanelRef = useRef(null)
  const resultsPanelRef = useRef(null)
  const pendingFocusViewRef = useRef(null)
  const meetingTriggerRef = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    getApiStatus({ signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return
        setApiHealth('healthy')
        setApiContext(snapshotContext(payload))
      })
      .catch((statusError) => {
        if (controller.signal.aborted || isAbortError(statusError)) return
        setApiHealth('offline')
        setApiContext(errorMessage(statusError, 'The UCSB schedule API is unavailable. Course search and generation may not work.'))
      })
    return () => controller.abort()
  }, [])

  useEffect(() => () => generationAbortRef.current?.abort(), [])

  useEffect(() => {
    if (pendingFocusViewRef.current !== activeView) return
    const panels = {
      courses: coursesPanelRef,
      preferences: preferencesPanelRef,
      results: resultsPanelRef,
    }
    window.requestAnimationFrame(() => panels[activeView].current?.focus())
    pendingFocusViewRef.current = null
  }, [activeView])

  const currentResult = results[activeResult]
  const navigateTo = (view, focusPanel = true) => {
    if (focusPanel) pendingFocusViewRef.current = view
    setActiveView(view)
    setSelectedMeeting(null)
    scrollToTop()
  }

  const closeMeeting = useCallback(() => {
    setSelectedMeeting(null)
    window.requestAnimationFrame(() => meetingTriggerRef.current?.focus())
  }, [])

  const selectMeeting = (meeting) => {
    meetingTriggerRef.current = document.activeElement
    setSelectedMeeting(meeting)
  }

  const cancelGeneration = () => {
    generationAbortRef.current?.abort()
    generationAbortRef.current = null
    generationRequestRef.current += 1
  }

  const invalidateResults = () => {
    cancelGeneration()
    setResults([])
    setActiveResult(0)
    setSelectedMeeting(null)
    setStatus('idle')
    setGenerationMeta(null)
    setError('')
  }

  const addCourse = (course) => {
    setSelectedCourses((courses) => courses.length < 5 && !courses.some((item) => item.code === course.code) ? [...courses, course] : courses)
    setAnnouncement(`${course.code} added. ${course.title}.`)
    invalidateResults()
  }

  const updatePreferences = (nextPreferences) => {
    setPreferences(nextPreferences)
    invalidateResults()
  }

  const removeCourse = (code) => {
    setSelectedCourses((courses) => courses.filter((course) => course.code !== code))
    setAnnouncement(`${code} removed.`)
    invalidateResults()
  }

  const resetPlanner = () => {
    cancelGeneration()
    setSelectedCourses([])
    setPreferences(createDefaultPreferences())
    setPlannerVersion((version) => version + 1)
    setActiveView('courses')
    setStatus('idle')
    setResults([])
    setActiveResult(0)
    setSelectedMeeting(null)
    setError('')
    setGenerationMeta(null)
    setAnnouncement('Planner reset. Choose your courses.')
    pendingFocusViewRef.current = 'courses'
    scrollToTop()
    window.requestAnimationFrame(() => coursesPanelRef.current?.focus())
  }

  const generate = async () => {
    generationAbortRef.current?.abort()
    const controller = new AbortController()
    generationAbortRef.current = controller
    const requestId = ++generationRequestRef.current
    setError('')
    setSelectedMeeting(null)
    setStatus('loading')
    setAnnouncement('Comparing course combinations.')
    pendingFocusViewRef.current = 'results'
    setActiveView('results')
    scrollToTop()
    try {
      const payload = await generateSchedules(selectedCourses, preferences, { signal: controller.signal })
      if (requestId !== generationRequestRef.current) return
      setResults(payload.schedules)
      setGenerationMeta(payload.meta)
      setActiveResult(0)
      setStatus('success')
      setAnnouncement(`${payload.schedules.length} ranked schedules are ready.`)
    } catch (generationError) {
      if (controller.signal.aborted || isAbortError(generationError) || requestId !== generationRequestRef.current) return
      setError(errorMessage(generationError, 'Schedule generation failed. Check the API connection and try again.'))
      setGenerationMeta(generationError.meta ?? null)
      if (generationError instanceof NoValidSchedulesError || generationError.code === 'NO_VALID_SCHEDULES') {
        setStatus('no-valid')
        setAnnouncement('No conflict-free schedules were found.')
      } else {
        setStatus('error')
        setAnnouncement('Schedule generation failed. Return to preferences and try again.')
        pendingFocusViewRef.current = 'preferences'
        setActiveView('preferences')
      }
    } finally {
      if (generationAbortRef.current === controller) generationAbortRef.current = null
    }
  }

  const resultsAvailable = status === 'loading' || status === 'success' || status === 'no-valid'

  return (
    <div className="app-shell">
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</div>
      <AppHeader onReset={resetPlanner} apiHealth={apiHealth} apiContext={apiContext} />
      <WorkflowTabs
        activeView={activeView}
        onChange={(view) => navigateTo(view, false)}
        selectedCount={selectedCourses.length}
        resultCount={results.length}
        resultsAvailable={resultsAvailable}
      />
      <main>
        <section ref={coursesPanelRef} className="workflow-panel course-window" id="workflow-panel-courses" role="tabpanel" aria-labelledby="workflow-tab-courses" tabIndex="-1" hidden={activeView !== 'courses'}>
            <header className="workflow-intro">
              <div><p className="eyebrow">01 / Course route</p><h2>Choose the classes in your quarter.</h2></div>
              <p>Search the Winter 2027 UCSB snapshot and build a route of two to five courses. You can come back and change it at any time.</p>
            </header>
            <section className="selection-deck">
              <CourseSearch key={plannerVersion} selected={selectedCourses} onSelect={addCourse} />
              <CourseRoute courses={selectedCourses} onRemove={removeCourse} />
            </section>
            <div className="panel-actions">
              <div className="panel-readiness">
                <strong>{selectedCourses.length >= 2 ? 'Course route ready' : `${2 - selectedCourses.length} more ${2 - selectedCourses.length === 1 ? 'course' : 'courses'} needed`}</strong>
                <span>{selectedCourses.length >= 2 ? 'Next, tell the optimizer what kind of week you want.' : 'Select at least two courses before continuing.'}</span>
              </div>
              <button className="primary-action" type="button" disabled={selectedCourses.length < 2} onClick={() => navigateTo('preferences')}>
                Continue to preferences <ArrowRight size={19} />
              </button>
            </div>
        </section>

        <section ref={preferencesPanelRef} className="workflow-panel preference-window" id="workflow-panel-preferences" role="tabpanel" aria-labelledby="workflow-tab-preferences" tabIndex="-1" hidden={activeView !== 'preferences'}>
            <header className="workflow-intro">
              <div><p className="eyebrow">02 / Preferences</p><h2>Decide what makes a schedule work for you.</h2></div>
              <p>Set your preferred days, time range, and minimum gap. Importance controls tell the optimizer which tradeoffs matter most.</p>
            </header>
            <div className="selected-route-summary" aria-label="Selected course route">
              <span>Current route</span>
              {selectedCourses.length ? selectedCourses.map((course, index) => <strong className={`color-${COURSE_COLORS[index]}`} key={course.code}>{course.code}</strong>) : <em>No courses selected yet</em>}
            </div>
            <Preferences preferences={preferences} onChange={updatePreferences} />
            <GenerateBand count={selectedCourses.length} status={status} onGenerate={generate} onBack={() => navigateTo('courses')} error={error} />
        </section>

        <section ref={resultsPanelRef} className="workflow-panel results-window" id="workflow-panel-results" role="tabpanel" aria-labelledby="workflow-tab-results" tabIndex="-1" hidden={activeView !== 'results'}>
            <div className="results-utility">
              <button className="secondary-action" type="button" onClick={() => navigateTo('preferences')}><ArrowLeft size={18} /> Tune preferences</button>
              <span>{selectedCourses.map((course) => course.code).join(' · ')}</span>
            </div>
            <section className={`schedule-workspace ${status === 'loading' ? 'is-loading' : ''}`} aria-busy={status === 'loading'}>
              {status === 'loading' && <div className="optimization-scan" role="status" aria-label="Comparing schedule combinations"><span /></div>}
              {status === 'no-valid' ? <NoValidWorkspace message={error} meta={generationMeta} /> : !currentResult ? <EmptyWorkspace /> : (
                <>
                  <div className="workspace-heading">
                    <div><p className="eyebrow">03 / Results</p><h2>Compare your strongest routes</h2></div>
                    <ResultNavigator results={results} activeIndex={activeResult} onSelect={(index) => { setActiveResult(index); setSelectedMeeting(null) }} />
                  </div>
                  <div id="schedule-panel" role="tabpanel" aria-labelledby={`schedule-tab-${activeResult}`} tabIndex="0">
                    <ScoreSummary result={currentResult} meta={generationMeta} />
                    <div className="calendar-stage">
                      <DesktopCalendar result={currentResult} selectedCourses={selectedCourses} selectedMeeting={selectedMeeting} preferredDays={preferences.days} onMeetingSelect={selectMeeting} />
                      <MobileAgenda result={currentResult} selectedCourses={selectedCourses} selectedMeeting={selectedMeeting} onMeetingSelect={selectMeeting} />
                      <MeetingDetails meeting={selectedMeeting} color={selectedMeeting ? COURSE_COLORS[Math.max(0, selectedCourses.findIndex((course) => course.code === selectedMeeting.courseCode)) % COURSE_COLORS.length] : 'aqua'} onClose={closeMeeting} />
                    </div>
                  </div>
                </>
              )}
            </section>
        </section>
      </main>
      <footer>
        <span>Winter 2027 UCSB snapshot · Student-built schedule planner</span>
        <button onClick={resetPlanner}><RotateCcw size={15} /> Reset planner</button>
      </footer>
    </div>
  )
}
