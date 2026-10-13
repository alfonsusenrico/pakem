# Surfaces: What the Interface Is For

Status: Active
Applies to: every user interface; the agent decides which surface a product mostly is
Last reviewed: 2026-10-13
Review by: 2027-04-13
Sources: Shneiderman, "The Eyes Have It" (1996, visual information-seeking mantra); Few, Information Dashboard Design (2nd ed., 2013); Ware, Information Visualization: Perception for Design (4th ed., 2021, pre-attentive features); Nielsen, 10 Usability Heuristics (Nielsen Norman Group); Nielsen Norman Group articles on F-pattern scanning, page dwell time, and progressive disclosure; Krug, Don't Make Me Think (3rd ed., 2014); W3C WCAG 2.2 (2.2.2 Pause, Stop, Hide; 2.3.3 Animation from Interactions); deceptive.design (Brignull) for dark patterns; owner statement 2026-10-13

## Method

1. Name the user's situation: who arrives, how often, and what they need to get done
   or understand.
2. Decide what the product mostly is. The three surfaces below are worked examples,
   not a closed list. A product can mix them (a web application with a dashboard
   page): apply each to the screens it fits. For another kind of product, write the
   same four parts (goal, questions, principles, checks) in the project's design notes.
3. Ask the surface questions in the brainstorm (`change-planning`) and record the
   answers in the design notes.
4. Verify with the surface checks plus the accessibility evidence.

## Data dashboard: reading and comparing a large amount of data

**Goal.** Users find what matters in a lot of data quickly, control what they see, and
can tell at a glance which data is which.

**Questions.** Who reads it, how often, and which decisions does it support? Which few
numbers answer "how are we doing"? What needs action? Which comparisons matter (period,
target, segment)? How fresh must the data be?

**Principles.**
- Overview first, zoom and filter, details on demand.
- Order the page as an answer: the conclusion and what needs attention, then the few
  key numbers, then the evidence (charts and tables), then drill-down.
- Navigation and controls stay in one predictable place; active filters are always
  visible; view state lives in the URL so a view can be shared and revisited.
- Give the data the space: dense but aligned, and nothing decorative competing with it.
- Make categories distinguishable at a glance with pre-attentive features (position,
  hue, size, shape), only a few categories at once, direct labels where possible, and
  the same colour for the same category in every chart. Highlight only what needs
  attention, so the eye goes there first.
- Pick the chart by the question: comparison as bars, change over time as lines, exact
  values as a table; offer a table view for every chart.
- Show the data source, its freshness, and uncertainty in plain words.

**Checks.** A first-time reader states the main takeaway within a minute; a named value
is found through the controls without help; categories are identified without hunting
in a legend; the page works at 320 CSS pixels.

## Public website or landing page: winning and informing strangers

**Goal.** A visitor understands within seconds what this is, for whom, and why it
matters; stays interested; takes the one intended action; and leaves with the key
message complete.

**Questions.** Who arrives, from where, and on which device? What must they understand
first? What is the one action we want? What proof makes it credible? Which messages
must not be missed?

**Principles.**
- Visitors decide quickly and scan rather than read: put the core message and the
  action at the top, in plain words, and make headings carry the story on their own.
- Tell it in order: the problem or promise, the proof, the invitation.
- One primary action, repeated where the story ends; no competing calls to action.
- Keep interest with rhythm, real imagery, and motion, never at the cost of reading:
  moving content can be paused, stops when offscreen, and reduced-motion preferences
  remove it.
- Real proof only: actual work, results, and testimonials used with permission.
- Fast first load and a phone-first layout, because many visitors arrive by phone.

**Checks.** A five-second test answers what it is, for whom, and what to do next; every
key message appears in the intended order; one primary action; performance and motion
safeguards verified.

## Web application: doing tasks and coming back

**Goal.** Users get their tasks done without confusion even as features grow, find
their data easy and pleasant to take in, and want to come back.

**Questions.** What are the three to five core tasks and how often are they done? Who
is new and who is expert? What do users check on every visit? What value brings them
back?

**Principles.**
- Organize navigation by the user's tasks, not by internal modules; core tasks are one
  step from the start screen; the current location is always clear.
- Progressive disclosure: show what most users need, reveal advanced options on demand.
- Consistency and recognition over recall: the same pattern for the same job
  everywhere, visible labels, no hidden gestures for essential actions.
- Clear feedback for every action; prefer undo to confirmation where it is safe; prevent
  errors rather than explain them.
- Show the user's data as the answer to their question first (a summary or trend), with
  details next; make good outcomes feel rewarding, honestly.
- Teach through useful empty states and next steps rather than long tours.
- Bring people back through real value (reminders they chose, progress they care
  about), never through dark patterns such as forced continuity, confirm-shaming, or
  hidden cancellation.

**Checks.** A new user completes each core task unaided; navigation labels match the
task names users use; every data view handles loading, empty, partial, and error states;
no dark patterns.
