# How this problem was actually approached

The chain of reasoning, including the parts that were wrong. Each stage is: what seemed
obvious, what broke it, and what replaced it.

The finished method looks tidy. It was not arrived at tidily, and the wrong turns are
where the understanding is.

---

## The question

A GP clinic group wants to open another site in Greater Sydney. Their model is mixed
billing, seven days, multidisciplinary. **Where?**

Reframed into something measurable: *which suburbs have too few GPs for the people who
must share them, among people who could plausibly use this particular kind of clinic?*

Everything below is the gap between those two sentences.

---

## 1. The obvious ratio, and the "+1" that destroyed it

First instinct: rank by `population / total_gp`. Fewest doctors per person wins.

Immediate snag — suburbs with zero GPs divide by zero. The reflex fix is
`population / (total_gp + 1)`.

That `+1` looks like housekeeping. It was the single decision that invalidated the first
answer, because **missing data had been recorded as `total_gp = 0`**. "We never looked
here" and "there are no doctors here" became the same number. 239 of 680 suburbs were
never successfully measured, and since the metric rewards low supply, they floated
straight to the top.

**7 of the original top 10 had never returned any supply data at all.**

> **The lesson:** before trusting any ratio, ask what the zeros in the denominator mean.
> A zero that means "absent" and a zero that means "unmeasured" cannot share a column.

---

## 2. Separating "none" from "unknown" — and creating a new problem

Replace the number with an explicit three-way status: `ok` (433 suburbs),
`gp_count_unknown` (8), `no_data` (239). Only measured suburbs may make a supply claim.

Correct, and immediately unsatisfying: 247 suburbs and 594,153 people are now excluded
from the analysis entirely. "I don't know" is honest but it isn't an answer.

This forced a better question: **is there a supply measure that doesn't depend on having
scraped that particular suburb?** There is, but it needs the next realisation first.

---

## 3. The Croydon moment — the unit of analysis was wrong

Croydon ranked first. One practice inside its boundary.

**Twenty-seven within two kilometres.**

Croydon sits wedged between Burwood and Ashfield. Nobody in Croydon is short of a GP; the
suburb is just small, so few practices happen to fall inside its polygon.

This is not a parameter that needs tuning. **Supply counted inside an administrative
boundary is a property of the boundary, not of access.** Patients do not stop at suburb
lines, so neither can the measure.

Confirmed independently afterwards: the Department of Health's GP catchments, derived from
Medicare patient flows, put Croydon, Burwood and Ashfield in a *single* catchment. The
government's own data says they are one market.

> **The lesson:** when a result is absurd, check whether the unit of analysis is answering
> a different question from the one you asked.

---

## 4. Choosing a replacement, and rejecting three

| Candidate | Why it was rejected |
|---|---|
| Practices within X km | X is arbitrary, and it ignores that those practices also serve everyone *else* within X km of them |
| Distance to nearest practice | Ignores capacity entirely — one GP next door beats twelve a suburb away |
| Plain gravity model | Right shape, but no principled functional form and no competition term |
| **E2SFCA** | **Chosen** |

Two-step floating catchment works like this. **Step one, per practice:** add up all the
population that can reach it, discounting each person by travel time, then divide the
practice's supply by that crowd. That gives "practices per weighted person competing
here". **Step two, per suburb:** add up that ratio across every practice the suburb can
reach, discounted the same way.

It won because it answers the actual question — *practices per person competing for them*
— rather than a proxy for it. It also solved stage 2: accessibility needs only a suburb's
population and location plus the practice register, never the scrape. **The 247 held-out
suburbs re-enter the analysis.**

---

## 5. Every spatial method has an edge, and the edge lies

Run E2SFCA over the 680 study suburbs and practices near the boundary get a truncated
demand pool — the people who would compete for them live outside the study area and are
invisible. Their supply-to-demand ratio explodes, and the fringe suburbs that can reach
them read as superbly served.

Measured: two Helensburgh practices, outside the study area, showed a weighted demand pool
of 1,467 people against four Woy Woy practices showing 60. Those two practices alone
supplied **72% of Heathcote's accessibility**, moving Heathcote from the 4th percentile to
the 98th.

Fix: extend the demand side with a ring of ABS SA2 population beyond the study boundary.
Step one sees everyone competing; step two reports only for the 680.

> **The lesson:** if your study area has a border, something is wrong at the border. Look
> there before you look anywhere else.

---

## 6. Thin supply is necessary, not sufficient

A suburb with few GPs is not automatically a good site. It also needs enough people, the
*right* people for this billing model, and somewhere to operate. So: four criteria, each
mapped to [0,1].

Two of them then went wrong in instructive ways.

### The income trap

First version: higher income scores higher. Seems reasonable — wealthier catchment,
healthier business.

It is backwards. The top 20 came out at the **90th income percentile** — exactly the
households who skip mixed billing and buy full private care. Mixed billing needs people
who can absorb roughly a $45 gap *and are not already paying more*.

So income is not monotone. It is an **inverted U** with a plateau in the middle.

> **The lesson:** for every variable, check whether "more is better" is actually true.
> Sometimes the optimum is in the middle, and a monotone score cannot represent that.

### The smallness trap

Point Piper, population 1,334, ranked first. Two causes: area was a weighted criterion
(smaller scored higher), and population was min-max normalised across a range dominated by
30,000-person suburbs, so a 1,300-person enclave was barely penalised.

Fixes: drop the area term, put demand on a **log ramp** so small is genuinely punished, and
add **hard eligibility gates** that cannot be compensated for — fail one and you get no
rank at all, however good your other scores.

> **The lesson:** min-max normalisation on a skewed variable barely penalises the tail.
> And some constraints should be gates, not weights.

---

## 7. "Why 0.40?" — the question with no good answer

Someone will ask why access carries 0.40 and operational 0.10. There is no defensible
answer. Every weight is a judgement.

Options considered: **AHP** produces weights that look derived while still encoding the
analyst's opinion — it launders judgement through a matrix. **Regression** would estimate
them from data, except there is no outcome variable anywhere in this problem: no revenue,
no patient volumes, no success labels.

So the weights stay asserted, and the claim changes instead. Vary every weight ±50% and to
zero, re-rank each time, and report **what survives**. The recommendation becomes "what is
robust", not "what wins".

That reframing is the single most useful move in the project.

---

## 8. Is the answer any good? Finding an external check

Everything so far is internally consistent. Internal consistency is not evidence.

The test needs something the model has never seen. The Department of Health's
**Distribution Priority Area** flag is exactly that — an independent under-supply
designation derived from GP FTE per capita.

AUC **0.813**. The accessibility surface reproduces the government's own classification
without ever having seen it. Satisfying.

Then, out of suspicion rather than virtue: what does a measure that knows *nothing* about
GP supply score?

**`-log(population)` alone: 0.849.** Higher.

DPA-positive suburbs have a median population of 815 against 5,643 for the rest. Anything
correlated with smallness scores well on this task. The 0.813 was substantially measuring
"is this a small suburb".

So isolate it: stratify by population band and ask whether discrimination survives with
size held roughly fixed. It does — **0.808 and 0.824** in the two bands with enough
positives. The measure is carrying real supply information.

But DPA has **zero positives above 15,000 residents**, and the shortlist spans 6,148 to
25,021.

> **The lesson:** a benchmark can be genuine, and the model can genuinely pass it, and the
> test can still not reach the decision you are actually making. Report all three.

---

## 9. The robustness check was flattering itself

31 perturbations, one knob moved per run, and Cronulla survives every one. That sounds
like strong evidence.

It is not, because **one-factor-at-a-time cannot see interactions.** A suburb only has to
survive 31 single deviations from the baseline, never a combination of them.

Drawing every knob jointly over the *same ranges*, 400 times: Cronulla holds rank 1 in
**55%** of draws. **Twelve** different suburbs take rank 1 in at least one draw, five of
them in more than 1% — Avalon Beach 20%, Glenmore Park 10%, Oran Park 8%, Caringbah South
5%.

> **The lesson:** OAT sensitivity is the standard robustness check and it systematically
> flatters whatever the baseline happens to be.

---

## 10. The method itself was never tested

Every one of those 31 perturbations varied a *parameter*. None varied the *model*.

3SFCA is a standard refinement of the same technique: it adds a demand-side competition
weight, so a suburb splits itself across the practices it can reach instead of
contributing its whole population to each. It is the stricter formulation — 2SFCA genuinely
double-counts demand across overlapping catchments.

Under 3SFCA: **Avalon Beach is rank 1 and Cronulla is rank 3.**

Eight of ten top-10 members survive the switch, so the shortlist is stable even though its
ordering is not.

> **The lesson:** sensitivity analysis usually tests settings. Test the method too.

---

## 11. What it still cannot do

The model measures **under-supply**, not **winnability**. It cannot distinguish a suburb
that is thin because existing practices are turning patients away — genuine unmet demand —
from one that is thin because the market never supported more. Those require opposite
decisions and look identical in this data.

Closing that needs appointment wait times or practice-level capacity. Neither is public.
That is the honest reason it remains open, not an oversight.

---

## Where it ends up

Not one suburb. A shortlist, with its confidence stated:

- **Cronulla** is the modal winner — rank 1 in 55% of joint draws, top 10 in all 32
  one-at-a-time perturbations
- **Avalon Beach, Oran Park, Glenmore Park** each hold rank 1 in a meaningful share
- The top 10 spans only **seven distinct GP catchments**, so it is seven opportunities
  presented as ten
- Ranks 9 and 10 are noise

And it narrows further on contact with reality: the model takes 680 suburbs down to seven
catchments worth visiting. The last step — ringing those practices to ask whether they are
accepting new patients — is a day's work and no public dataset can replace it.

---

## The shape of the whole thing

Every stage followed the same pattern. **Something obvious was tried. It produced an
absurd answer. The absurdity pointed at an assumption nobody had stated.**

- Croydon at #1 pointed at the unit of analysis
- Point Piper at #1 pointed at the normalisation
- A top 20 at the 90th income percentile pointed at monotonicity
- 7 unmeasured suburbs in the top 10 pointed at what zero means
- An AUC beaten by `log(population)` pointed at a confound
- 55% instead of 100% pointed at the sensitivity design

The method is not the achievement. **Noticing that each answer was absurd, and chasing why,
is the achievement.** The method is just what was left standing afterwards.
