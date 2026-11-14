"""
Chat with the coach: sessions, the context the model sees, and the two reply
paths (SSE stream and one-shot JSON).

Lived in main.py until 2026-11-14; its endpoints are thin wrappers now. The
knowledge base and the session factory are passed in — both are owned by
main.py, and the stream saves the reply on its own session after the request's
has closed.
"""
import json

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.models.database import Athlete, ChatMessage, ChatSession, week_plan
from backend.utils.timezone import get_local_now, get_local_today


CHAT_HISTORY_MESSAGES = 20   # newest messages sent to the model


def build_chat_context(db: Session, summary: str, rag_context: str, extras: str = "") -> str:
    from backend.services.periodization_engine import PeriodizationEngine
    from backend.services.plan_normalizer import normalize_plan
    from datetime import timedelta
    import json

    engine = PeriodizationEngine()
    training_context = engine.compute_context(db)
    
    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())
    plan_record = week_plan(db, start_of_week)
    plan_json = {}
    if plan_record:
        plan_json = normalize_plan(plan_record.plan_json)
        
    # The engine emits 'phase' — reading 'current_phase' meant chat said
    # "TRAINING PHASE: Unknown" since forever (found 2026-08-31).
    context_str = f"TRAINING PHASE: {training_context.get('phase', 'Unknown')}\n"
    if training_context.get("race_done"):
        context_str += "GOAL RACE: already run — no race on the calendar\n"
    else:
        context_str += f"WEEKS TO RACE: {training_context.get('weeks_to_race', 'N/A')}\n"
    
    today_day_name = get_local_now().strftime("%A")
    if plan_json and "days" in plan_json:
        today_plan = plan_json["days"].get(today_day_name)
        context_str += f"\nTODAY'S WORKOUT ({today_day_name}):\n"
        if today_plan:
            context_str += json.dumps(today_plan, indent=2)
        else:
            context_str += "Rest day / No workout planned."
            
        context_str += "\n\nFULL WEEK SCHEDULE:\n"
        for day, info in plan_json["days"].items():
            context_str += f"- {day}: {info.get('summary', info.get('focus', 'Rest'))}\n"
            
    system_prompt = f"""You are Phoenix, an elite personal coach. You know this athlete's exact schedule, zones, and goals. You talk like a real coach — direct, confident, no fluff.

ATHLETE DATA & ZONES:
{summary}

RECENT DETAIL (COROS, Python-computed):
{extras or "  (none yet)"}

{context_str}

COACHING KNOWLEDGE:
{rag_context}

RULES:
- Reply in the language of the athlete's most recent message — he code-switches between Spanish and English.
- Lead with the answer, then give 1-2 key reasons using the athlete's actual numbers or their scheduled workouts.
- Maximum 4-6 lines total. No headers, no essays, no bullet-point lists longer than 3 items.
- If the athlete asks a yes/no question, start with yes or no.
- Reference specific data points (Pace Zones, HR Zones, Today's Workout, etc.) to justify your advice.
- Sound like a coach in person, not a textbook.
- If they ask what to do today, refer directly to TODAY'S WORKOUT."""
    return system_prompt


def list_sessions(db):
    """Return all chat sessions for the athlete."""
    athlete = db.query(Athlete).first()
    if not athlete:
        return []
    sessions = db.query(ChatSession).filter(ChatSession.athlete_id == athlete.id).order_by(ChatSession.updated_at.desc()).all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "updated_at": str(s.updated_at)
        } for s in sessions
    ]

def create_session(db):
    """Create a new empty chat session."""
    athlete = db.query(Athlete).first()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found")
    new_session = ChatSession(athlete_id=athlete.id)
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    return {"id": new_session.id, "title": new_session.title}

def session_history(db, session_id: int):
    """Return the full message history for a session."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at.asc()).all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": str(m.created_at)
        } for m in messages
    ]

def delete_session(db, session_id: int):
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session:
        db.delete(session)
        db.commit()
    return {"status": "ok"}


async def stream_reply(db, body: dict, kb, session_factory):
    """
    Chat with the AI coach — streams tokens via Server-Sent Events (SSE).
    
    The response is a stream of SSE events:
      data: {"token": "Hello"}
      data: {"token": " world"}
      ...
      data: [DONE]
    """
    from backend.agents.data_agent import DataAgent
    
    message = body.get("message", "")
    session_id = body.get("session_id")
    
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
        
    athlete = db.query(Athlete).first()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found")
        
    # Get or create session
    chat_session = None
    if session_id:
        chat_session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not chat_session:
        # Create a new session and auto-generate a title based on the first message
        title = message[:30] + "..." if len(message) > 30 else message
        chat_session = ChatSession(athlete_id=athlete.id, title=title)
        db.add(chat_session)
        db.commit()
        db.refresh(chat_session)
        session_id = chat_session.id
    else:
        # Update timestamp
        from datetime import datetime
        chat_session.updated_at = datetime.utcnow()
        db.commit()
        
    # Save user message
    user_msg = ChatMessage(session_id=session_id, role="user", content=message)
    db.add(user_msg)
    db.commit()
    
    # Build athlete context
    data_agent = DataAgent(db)
    summary = data_agent.summarize()
    
    # Get relevant knowledge from singleton KB
    rag_chunks = kb.query(message, n_results=3)
    rag_context = "\n\n".join(rag_chunks) if rag_chunks else ""
    
    system_prompt = build_chat_context(db, summary, rag_context, data_agent.chat_extras())

    messages = [{"role": "system", "content": system_prompt}]

    # Fetch history
    # Bounded history: Groq admits a request at prompt + completion cap against
    # 8000 TPM, so an unbounded session would eventually 429 the stream.
    history = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)\
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(CHAT_HISTORY_MESSAGES).all()[::-1]
    for h in history:
        messages.append({"role": h.role, "content": h.content})

    # Issue triage: does this message report soreness or an injury worth a plan
    # change? The keyword gate is pure Python so ordinary chat costs nothing
    # extra; only a likely hit pays for the extraction call. The proposal rides
    # out on the same stream — no second round trip on a cold-start-prone tier.
    proposal = None
    try:
        from backend.services.issue_triage import build_proposal, extract_issue, looks_like_issue
        if looks_like_issue(message):
            issue = extract_issue(message)
            if issue:
                proposal = build_proposal(db, issue)
    except Exception as e:
        # Triage is an enhancement. Chat must still work if it breaks.
        print(f"⚠️ Issue triage failed (continuing with normal chat): {e}")

    # Recovery triage — the same door swinging out. Only when issue triage
    # found nothing ("calf hurts again" must win over "calf is fine"), only
    # when the message sounds like good news, and only when something is
    # actually active to resolve — then it's worth one extraction call.
    recovery_proposal = None
    if not proposal:
        try:
            from backend.services.issue_triage import (
                build_recovery_proposal, extract_recovery,
                get_open_injuries, looks_like_recovery,
            )
            athlete = db.query(Athlete).first()
            if athlete and looks_like_recovery(message):
                # Open = Active + Recovering. An auto-expired window parks the
                # injury in Recovering, which is exactly the state waiting for
                # the athlete to say "it's fine now".
                open_injuries = get_open_injuries(db, athlete.id)
                if open_injuries:
                    matched = extract_recovery(message, open_injuries)
                    if matched:
                        recovery_proposal = build_recovery_proposal(db, matched)
        except Exception as e:
            print(f"⚠️ Recovery triage failed (continuing with normal chat): {e}")

    # Travel triage — third in line, and last on purpose: "hurt my knee on a
    # trip" is an issue, "calf's fine, flying out tomorrow" is a recovery, and
    # only a message that is neither gets read as pure logistics.
    travel_proposal = None
    if not proposal and not recovery_proposal:
        try:
            from backend.services.issue_triage import (
                build_travel_proposal, extract_travel, looks_like_travel,
            )
            if looks_like_travel(message):
                travel_days = extract_travel(message)
                if travel_days:
                    travel_proposal = build_travel_proposal(db, travel_days)
        except Exception as e:
            print(f"⚠️ Travel triage failed (continuing with normal chat): {e}")

    if travel_proposal:
        messages.append({
            "role": "system",
            "content": (
                "The athlete just said they will be traveling on "
                f"{', '.join(travel_proposal['days'])}. A confirmation card is "
                "already being shown to them offering to rest those days and "
                "rebuild the open days around the trip (run volume protected), "
                "so do NOT propose specific rearrangements yourself. Reply in "
                "one or two sentences: acknowledge the trip and tell them to "
                "confirm below."
            ),
        })

    if recovery_proposal:
        rebuild = recovery_proposal.get("rebuild_days") or []
        messages.append({
            "role": "system",
            "content": (
                "The athlete just said their "
                f"{recovery_proposal['injury']['body_part']} has recovered. "
                "A confirmation card is already being shown to them"
                + (f" offering to rebuild {', '.join(rebuild)}" if rebuild else "")
                + ", so do NOT propose specific sessions yourself. Reply in one "
                "or two sentences: celebrate briefly, remind them to ease back "
                "in, and tell them to confirm below."
            ),
        })

    if proposal:
        # Keep the prose and the card from contradicting each other. The card
        # owns the specifics; the reply is the human part around it.
        affected = ", ".join(d["day"] for d in proposal["affected_days"])
        messages.append({
            "role": "system",
            "content": (
                "The athlete just reported a physical issue "
                f"({proposal['issue']['body_part']}, severity "
                f"{proposal['issue']['severity']}/10). A confirmation card is already "
                f"being shown to them listing the affected days ({affected}) with "
                "swap-or-rest options for each, so do NOT list those days or propose "
                "specific replacement sessions yourself. Reply in two or three "
                "sentences: acknowledge it, say briefly what you'd do about it and "
                "why, and tell them to confirm the changes below."
            ),
        })

    async def event_stream():
        """Generator that yields SSE events with streamed tokens."""
        full_response = ""
        try:
            # First frame: which session this exchange was filed under. A
            # client that opened with no session_id can only continue the
            # conversation if this comes back — without it every message
            # minted its own one-message session. Unknown keys are ignored
            # by clients that don't parse them.
            yield f"data: {json.dumps({'session_id': session_id})}\n\n"
            from backend.core.llm_client import chat_completion_stream
            async for token in chat_completion_stream(messages):
                full_response += token
                yield f"data: {json.dumps({'token': token})}\n\n"
            # Emitted before [DONE] so the client can attach the confirmation
            # card to this message. Clients that don't know the key ignore it.
            if proposal:
                yield f"data: {json.dumps({'proposal': proposal})}\n\n"
            if recovery_proposal:
                yield f"data: {json.dumps({'recovery': recovery_proposal})}\n\n"
            if travel_proposal:
                yield f"data: {json.dumps({'travel': travel_proposal})}\n\n"
            yield "data: [DONE]\n\n"

            # Save assistant response
            db_save = session_factory()
            try:
                ai_msg = ChatMessage(session_id=session_id, role="assistant", content=full_response)
                db_save.add(ai_msg)
                db_save.commit()
            finally:
                db_save.close()
        except Exception as e:
            print(f"LLM STREAMING ERROR: {e}")
            fallback = f"Coach is temporarily unavailable. Error: {str(e)[:100]}"
            yield f"data: {json.dumps({'token': fallback})}\n\n"
            yield "data: [DONE]\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering if ever behind proxy
        }
    )


async def sync_reply(db, body: dict, kb):
    """Non-streaming fallback chat endpoint. Returns the full response at once."""
    from backend.agents.data_agent import DataAgent
    
    message = body.get("message", "")
    session_id = body.get("session_id")
    
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
        
    athlete = db.query(Athlete).first()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found")
        
    # Get or create session
    chat_session = None
    if session_id:
        chat_session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not chat_session:
        title = message[:30] + "..." if len(message) > 30 else message
        chat_session = ChatSession(athlete_id=athlete.id, title=title)
        db.add(chat_session)
        db.commit()
        db.refresh(chat_session)
        session_id = chat_session.id
    else:
        from datetime import datetime
        chat_session.updated_at = datetime.utcnow()
        db.commit()
        
    # Save user message
    user_msg = ChatMessage(session_id=session_id, role="user", content=message)
    db.add(user_msg)
    db.commit()
    
    # Build athlete context
    data_agent = DataAgent(db)
    summary = data_agent.summarize()
    
    # Get relevant knowledge from singleton KB
    rag_chunks = kb.query(message, n_results=3)
    rag_context = "\n\n".join(rag_chunks) if rag_chunks else ""
    
    system_prompt = build_chat_context(db, summary, rag_context, data_agent.chat_extras())

    messages = [{"role": "system", "content": system_prompt}]
    
    # Fetch history
    # Bounded history: Groq admits a request at prompt + completion cap against
    # 8000 TPM, so an unbounded session would eventually 429 the stream.
    history = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)\
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(CHAT_HISTORY_MESSAGES).all()[::-1]
    for h in history:
        messages.append({"role": h.role, "content": h.content})

    try:
        from backend.core.llm_client import chat_completion
        content = chat_completion(messages=messages)
        
        # Save assistant message
        ai_msg = ChatMessage(session_id=session_id, role="assistant", content=content)
        db.add(ai_msg)
        db.commit()
        
        return {"response": content, "session_id": session_id}
    except Exception as e:
        print(f"LLM ERROR in /chat-sync: {e}")
        return {"response": f"I'm currently in offline mode. Error: {str(e)[:100]}",
                "session_id": session_id}
