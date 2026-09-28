"""The five RPSAS Method micro-lessons — real teaching content, ~3-minute
reads, unlocked in order (see models.MICRO_LESSONS). Framework-free, imported
by api/routes/practice.py."""

LESSONS = {
    "read": {
        "title": "Read",
        "subtitle": "Room, Emotion, Angle, Desire",
        "body": """\
Before you say a single word, you've already been handed four pieces of information. Most people walk past all four and start talking anyway. That's the single biggest reason good clinical knowledge lands badly — not because the content was wrong, but because nobody read the room first.

**Room.** What does the physical space tell you? A patient sitting on the edge of the exam table, coat still on, phone in hand, is not settled in. A family member standing instead of sitting has already decided this conversation is going to be short, or is bracing for something. The room is talking before anyone opens their mouth — the only question is whether you're listening to it.

**Emotion.** What's the temperature in here — not what should it be, what actually is it? Flat affect after a diagnosis usually means shock, not acceptance. A voice pitched too evenly and too fast is often fear wearing composure as a disguise. Anger is almost always a second emotion, not a first one — it's usually standing in for fear, grief, or having been dismissed once already today. Name the emotion to yourself before you respond to it.

**Angle.** Everyone walks in with a frame already built — a story they're already telling themselves about what's happening. A patient who Googled their symptoms for three days has an angle. A family member who lost a parent to a similar diagnosis has an angle. You don't have to agree with the angle. You have to know it's there, because if you talk past it, they'll hear you as talking past *them*.

**Desire.** Underneath all of it — what does this person actually want from the next five minutes? Not what they *say* they want (that's often Proof — "give me the data"), but what they actually need to walk out feeling like the conversation went well. Sometimes it's information. Often it's control. Frequently it's just to be taken seriously before anything else happens.

**Why this comes first.** Every mistake in the next four steps of the method traces back to skipping this one. You can't Pick the right P if you never read what's actually in the room. You can't Speak well if you're pitched to the wrong Emotion. You can't Ask a real question if you never located the Desire. Read is not a soft-skills warm-up before the "real" clinical conversation — it *is* the conversation, in miniature, before the conversation.

**Try this next time:** before your first sentence, take one full second and silently name all four — Room, Emotion, Angle, Desire — even if your answer is just a guess. A fast wrong guess, corrected in real time, still beats no guess at all.""",
    },
    "pick": {
        "title": "Pick the P",
        "subtitle": "Proof, Plan, Permission, Power",
        "body": """\
Once you've read the room, you're holding a diagnosis of your own: which of four things is this person actually asking for? Get this wrong and you'll answer a question nobody asked — technically correct, and it'll still fall flat.

**Proof.** They want evidence before they'll trust anything else you say. Data, statistics, your track record, a second opinion that matches yours. This is the patient who says "show me the study" or "how many of these have you done." Give a Proof person reassurance without evidence and they'll hear it as you dodging the question — because to them, it is.

**Plan.** They don't want reassurance, they want sequence. What happens today. What happens tomorrow. What happens next week if this goes wrong. A Plan person is often the calmest-looking person in the room and the most anxious underneath it — the anxiety is *specifically* about not knowing the next step, so the fix is specifically a next step, not a feeling.

**Permission.** They need this to still feel like their decision. Not because they're indecisive — because someone, somewhere, already made a decision for them today, and they need this one to be theirs. Talk *at* a Permission person with a recommendation and they'll resist it on principle, even if they agree with it. Ask them, instead of telling them, and watch the resistance disappear.

**Power.** They need to feel like they still have agency over what's happening to their own body, their own family member, their own situation — not agency over the medical decision itself, agency over *how it's handled*. Give a Power person options, even small ones — which arm, which day, who's in the room — and the whole interaction changes register.

**How to actually tell them apart.** Listen to the first full sentence someone says, unprompted. "What does the research say" is Proof. "So what happens next" is Plan. "I need to think about this" is Permission. "I'm not doing this if—" is Power. It's rarely subtle once you're listening for it instead of listening for the next thing you want to say.

**The trap.** Most clinicians default to whichever P they'd personally want if they were the patient. If you're a Plan person by nature, you'll hand every patient a sequence of steps — including the Permission patient who just needed to be asked. Picking the P is not about what would reassure *you*. It's a read on someone else, made with someone else's wiring, not yours.

**Try this next time:** after Reading the room, say the P mode to yourself in one word before you speak. If you're not sure, ask a single open question and listen to which of the four shows up in the answer.""",
    },
    "speak": {
        "title": "Speak",
        "subtitle": "Sit, Pace, Eyes, Air, Kill the fillers",
        "body": """\
You can read the room perfectly and pick the right P and still lose the moment in the first ten seconds of delivery. Speak is mechanics — the physical, unglamorous part of the method — and it's the part people skip because it feels beneath the "real" clinical content. It isn't. Delivery is the container the content arrives in, and a cracked container spills the content no matter how good it is.

**Sit.** If there's a chair, use it. Standing over a seated patient is a physical power position whether you intend it that way or not, and in a bad-news conversation specifically, it reads as *rushed* even when you have all the time in the world. Sitting is the fastest, cheapest signal you have that says "I'm not about to leave."

**Pace.** Slow down more than feels natural. Under stress, everyone's internal clock speeds up — yours included — so "normal pace" under pressure is already fast. Deliberately pace as if you have more time than you do. The patient can't absorb information faster than they can process the fact that they're being given it.

**Eyes.** Hold eye contact through the hard sentence, not just around it. The instinct under stress is to look down at the chart, the tablet, your own hands right as you deliver the hardest line — precisely the moment eye contact matters most. Practice keeping your eyes up through the sentence that's hardest to say, not just the easy ones around it.

**Air.** Silence is not a gap to fill. After a hard sentence, stop talking and let it land. The instinct to keep talking through silence is almost always about *your* discomfort, not their need for more information. The pause is where they process. Taking it away from them to soothe your own discomfort is the single most common unforced error in this whole method.

**Kill the fillers.** "Um," "so," "basically," "I mean," "kind of" — every filler word is a tiny flinch, and patients read flinching as uncertainty about the medicine, even when the uncertainty is really just about the delivery. You don't have to sound rehearsed. You do have to sound like you believe your own sentence all the way to the period.

**Why this is five things and not one.** Each piece fails independently. You can nail the pace and still break eye contact at the worst moment. You can hold eye contact and still fill every pause with noise. Drill them one at a time — pick the one that's weakest for you specifically, and work only that one for a week before moving to the next.

**Try this next time:** pick your single weakest of the five right now, before your next real conversation, and name it. You'll catch yourself in the moment far more often once you know which one to watch for.""",
    },
    "ask": {
        "title": "Ask",
        "subtitle": "Acknowledge, Stop, Know what they want",
        "body": """\
Speak delivers what you came to say. Ask closes the loop on what *they* need to say back — and most encounters end without ever actually doing this. The clinician says their piece, the patient nods, everyone leaves, and nothing was actually confirmed. Ask is three small moves that take less than thirty seconds and change the entire outcome of the encounter.

**Acknowledge.** Before you ask anything, name what just happened. "That's a lot to take in." "I know that's not what you were hoping to hear." This isn't a script line — it's a real, specific acknowledgment of the actual moment, and it does something concrete: it tells the other person you registered the weight of what you just said, instead of moving straight past it to logistics.

**Stop.** After you acknowledge, stop talking. Fully. This is Air's cousin, but it's not about pacing a sentence — it's about ending your turn completely and handing the floor over. Most people rush this because silence after bad news feels unbearable to sit in. Sit in it anyway. If you fill it, you've taken the floor back before they were done processing, and whatever they needed to say gets swallowed.

**Know what they want.** Close with a real question, not a rhetorical one. Not "does that make sense?" — that's a comprehension check disguised as an invitation, and everyone answers "yes" to it whether or not it's true. Ask something that actually requires them to tell you something: "What's the first thing on your mind right now?" "What do you need from me before you leave today?" The answer tells you whether you actually landed the conversation or just delivered a monologue that sounded like one.

**Why this is the step people skip.** Ask requires tolerating discomfort on purpose, twice — once in the silence after Stop, and once in genuinely not knowing what the answer to your real question will be. Speak is performance; you can rehearse it. Ask is exposure; you can't fully script the other side of it. That's exactly why it matters — it's the only step in the method where you find out, in real time, whether the first four actually worked.

**Try this next time:** replace whatever closing line you currently default to with one specific, real question that requires an actual answer — then hold Stop long enough to get one.""",
    },
    "shift": {
        "title": "Shift",
        "subtitle": "See, Hold, Identify, Flip, Test",
        "body": """\
Every scenario so far has assumed the patient stays in one P mode for the whole encounter. Real encounters don't work that way. A Proof patient who gets the data they asked for often *becomes* a Plan patient the moment they believe you — now they want to know what happens next. An angry Power patient, once they're handed a real choice, can soften into Permission. The shift is not a curveball. It's the normal shape of a real conversation, and it's the part every other communication framework leaves out.

**See it.** The first job is noticing the shift is happening at all — which is hard, because you're usually mid-sentence, mid-plan, still executing the read you made two minutes ago. The tell is almost always in the question they ask next: if it doesn't fit the P mode you diagnosed, that's not them being inconsistent, that's the mode changing.

**Hold.** Don't panic-pivot on a hunch. One sentence that sounds like a shift can just be a stray thought. Hold your current approach for one more exchange and watch for a second confirming signal before you commit to having read a shift correctly. Reacting to a false shift costs you more credibility than missing a real one by one beat.

**Identify.** Once you're sure, name the new mode to yourself the same way you did the first time, in Pick. What are they asking for *now* — not five minutes ago. The four P's don't change; which one is active does.

**Flip.** Change your approach to match, out loud, in what you say next — not by announcing "I sense you want something different now" (that reads as clinical and strange), but by simply *delivering* the new mode. If they've flipped from Proof to Plan, stop citing data and start giving sequence. The flip should be invisible in your language and obvious in your effect.

**Test.** Confirm the flip landed the way Ask confirms the whole encounter landed — with a real question, not a guess. "Does that timeline work for what you need right now?" tells you whether you read the shift correctly, the same way the original Ask told you whether you read the original mode correctly.

**Why this is the hardest step, and the one that separates good from great.** The first four steps of the method are executable in advance — you can prepare a Read, prepare for a P, drill your Speak mechanics, script an Ask. Shift can't be prepared for, only practiced, because it only exists in real time, in response to a real person changing in front of you. It's the reason this whole method ends in a step about adapting, instead of a step about delivering — because the encounters that actually matter rarely go according to the plan you walked in with.

**Try this next time:** in your very next real encounter, treat one unexpected question as a possible shift instead of an interruption. See it, hold for one more beat, then decide.""",
    },
}

LESSON_ORDER = ("read", "pick", "speak", "ask", "shift")


def render_lesson_html(body):
    """Converts the lesson body's minimal markdown (paragraphs, **bold**,
    *italic*) into HTML. Deliberately not a full markdown dependency — the
    lesson content only ever uses these three constructs. Bold is converted
    before italic so **word** doesn't leave stray single asterisks behind for
    the italic pass to pick up."""
    import html
    import re

    paragraphs = body.strip().split("\n\n")
    out = []
    for para in paragraphs:
        escaped = html.escape(para)
        bolded = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        italicized = re.sub(r"\*(.+?)\*", r"<em>\1</em>", bolded)
        out.append(f"<p>{italicized}</p>")
    return "\n".join(out)
