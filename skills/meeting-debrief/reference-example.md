<!-- Reference example for /meeting-debrief. Illustrates correct emphasis — not a retelling, but analysis of what was hidden. Not to be edited — this is a historical reference, not a working config. Personal data anonymized. -->

# Call Summary "Project Phoenix" — (reference example)

**Participants:** the lead consultant (consulting firm), university rep (Phoenix), the AI engineer.
**Client:** a regional university. Progressive self-positioning, recently equipped a lab with servers/GPU, but no staff who can work with it. No AI subscriptions (local or international). The contact is warm (director introduces themselves by first name).

Two independent tasks surfaced in the call.

## Task 1. Auto-update of study plans ("translator from ministerial to human" + table filler)

Once a year a letter arrives from the ministry (likely PDF) with changes to program requirements. A lecturer manually edits 4–5 documents per discipline (Word/Excel), where ~4 recurring parameters change: hours (lecture/lab/in-class), indicators, etc. Documents — multi-tab Excel files where you have to find your discipline code among dozens of sheets.

Scale of pain: out of 74 interviews 68 people called it "a nightmare, pain". Constituency of lecturers is 50+. 102 lecturers in the branch, 10 departments, 60–80% of programs change something annually.

Key volume uncertainty: how many study plans — 100 or 800? The rep doesn't know (multiplied by full-time/part-time/courses/grad). That's an 8-fold range directly hitting the estimate.

Data: no personal data — only discipline codes; names are in orders at most. So no strict locality requirement formally.

Interface: web (not Telegram — VPN issues).

There was a ready solution (likely from a tender) — worked poorly, doing it manually still.

Timeline: project start July 1 if agreed; June 28 — defense of the research part, then full project. Budget "within current", possible negotiations — the rep needs a ballpark to budget.

Next step: the rep sends documents + list of changing parameters today. We cross-reference, do a test run, sketch out phases and rough numbers.

## Task 2. Project-based learning management + AI participant assistant

The university is launching an engineering school with project-based learning (real industry cases). Structure like in project work frameworks: teams → meetings with mentor (weekly), task-holder (every 3 weeks), experts. Five roles, three tracks: project (up to 70% of participants), community, faculty talent pool.

They want a system showing which stage each team is at, ability to select/book an expert, auto-reminders.

Scale: ~60–80 students, 10 mentors, 6–8 task-holders, 60–80 faculty (selectively).

Key insight on the call: the problem was split into two distinct things and the conversation wasn't steered toward "let's code a dashboard from scratch":

- LMS selection — funnel of stages, work statuses, reviews, analytics. This is a ready class of solutions; vibe-coding from scratch for one client is expensive and buggy. Candidate — an existing LMS platform (the consultant has a contact there for a good price), plus alternatives.
- AI participant assistant (buddy/curator) — text→voice chatbot: interviews at start, sends reminders, answers questions, connects participants by competency, collects analytics for administration. Technically — RAG with resume/work indexing, ~2 months, pilot in a month on one category. This is the real development work.

Next step: the rep sends the client program presentation (no ready TOS). The consultant drafts a TOS for the LMS using Claude, the rep edits (her part — by Thursday). Simultaneously — go for prices from the LMS contact + at least one more person.

## What stands out (and what wasn't discussed in the call)

**Main contradiction — timeline.** Start July 1, defense June 28, but the people who have both the pain and the answers (deputy for study programs) are on vacation and unreachable until September. You can't properly estimate Task 1 until you get a sample "ministerial letter → explanatory note → final edit" from them. The rep hasn't gotten this yet. Without it you're estimating blind — and that's the rule "don't invent numbers".

**"We'll sell to all universities" — that's a lure, not a plan.** Even on the call there was that feeling of "in theory yes, but there's a nuance" and a real example of another university with an advanced IT director who still moves an order of magnitude slower than commercial clients. This should be stated more firmly: the nearest measurable value is the pain of one branch, not a scalable product. Scalability goes into the architecture (which is why refusing the GPT black box is the right move), not into the budget justification. Otherwise you'll inflate your own expectations.

**On-prem on their GPU — it's a "feature", not a requirement.** No one from the university side asked for locality. Idle hardware is a nice "help them be progressive" argument, but it adds cost and complexity to a task that's essentially find&replace in Excel. Decide consciously: this is an upsell story as a separate layer, not a baseline scenario. Don't burden the first estimate with it.

**LMS recommendation cuts your own scope — and that's right.** Be honest with yourself: by recommending a ready LMS, you're cutting the volume of your own development (and revenue) in exchange for a better result for the client. The real development build is only the AI assistant. That's a valid trade-off, but don't let it quietly turn "the project" into "a small chatbot in a month" — then the economics of entry may not work.

**What wasn't discussed at all: is this one contract or three?** On the table are three different deliverables (study plans / LMS selection / AI assistant) with a murky budget — "within current", "maybe negotiations". Before calculating anything, extract from the rep: one contract or several, who pays, is there a procurement procedure. That determines how to split into phases and whether a pilot can be embedded.
