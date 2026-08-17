import React, { useEffect, useMemo, useRef, useState } from "react";
import "./ChatLive.css";
import { apiGet, apiPost, apiUpload, downloadFile, API_BASE, getToken } from "./api";

type MeOut = { id: string; role_key: string; department_id: string; section_id: string; perms: string[] };

type ChatItem = {
  id: number;
  title: string;
  department_id: string;
  section_id: string;
  created_by?: string;
  created_at?: string;
  accessible?: boolean;
  unread_count?: number;
};

type Attachment = { id: number; name: string; mime_type: string; size_bytes: number; url: string };
type MsgOut = { id?: number; chat_id?: number; user_id: string; text: string; created_at?: string; attachments?: Attachment[] };

function initials(id: string) {
  const s = (id || "U").replace(/[^a-zA-Z0-9]/g, "");
  return (s.slice(0, 2) || "U").toUpperCase();
}

function fmtTime(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function fmtDateTime(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString();
}

function isMobileWidth() {
  return window.matchMedia && window.matchMedia("(max-width: 860px)").matches;
}

export default function ChatLive() {
  const [me, setMe] = useState<MeOut | null>(null);
  const [chats, setChats] = useState<ChatItem[]>([]);
  const [q, setQ] = useState("");
  const [activeId, setActiveId] = useState<number | null>(null);

  const [messages, setMessages] = useState<MsgOut[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  const [loadingChats, setLoadingChats] = useState(false);
  const [loadingChat, setLoadingChat] = useState(false);
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);

  // ✓✓ visual v1
  const [seenMyIds, setSeenMyIds] = useState<Set<number>>(new Set());

  // typing v1
  const [typingUsers, setTypingUsers] = useState<Record<string, number>>({}); // user_id -> timestamp

  // ✅ mobile drawer
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [mobile, setMobile] = useState(isMobileWidth());

  const msgsRef = useRef<HTMLDivElement | null>(null);
  const shouldAutoScrollRef = useRef(true);

  const wsRef = useRef<WebSocket | null>(null);
  const typingTimerRef = useRef<number | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return chats;
    return chats.filter((c) => (c.title || "").toLowerCase().includes(s));
  }, [chats, q]);

  const activeChat = useMemo(() => chats.find((c) => c.id === activeId) || null, [chats, activeId]);

  const readOnly = useMemo(() => activeChat?.accessible === false, [activeChat]);

  async function loadMeAndChats() {
    setError("");
    setLoadingChats(true);
    try {
      const meData = await apiGet<MeOut>("/me");
      setMe(meData);

      const list = await apiGet<ChatItem[]>("/chats");
      setChats(list);

      if (!activeId && list.length) setActiveId(list[0].id);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoadingChats(false);
    }
  }

  async function loadChat(chatId: number) {
    setError("");
    setLoadingChat(true);
    try {
      const data = await apiGet<any>(`/chats/${chatId}`);
      const msgs: MsgOut[] = Array.isArray(data?.messages) ? data.messages : [];
      setMessages(msgs);
      const last = msgs.length ? Number(msgs[msgs.length - 1]?.id || 0) : 0;
      if (last > 0) apiPost(`/chats/${chatId}/read`, { last_read_id: last }).catch(() => {});

      setSeenMyIds((prev) => {
        const next = new Set(prev);
        for (const mm of msgs) {
          if (me?.id && mm.user_id === me.id && typeof mm.id === "number" && mm.id > 0) next.add(mm.id);
        }
        return next;
      });
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoadingChat(false);
    }
  }

  async function send() {
    const chatId = activeId;
    if (!chatId) return;
    const t = text.trim();
    if (!t) return;

    setSending(true);
    setError("");

    try {
      shouldAutoScrollRef.current = true;

      const tempId = -Date.now();
      const optimistic: MsgOut = {
        id: tempId,
        chat_id: chatId,
        user_id: me?.id || "ME",
        text: t,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, optimistic]);
      setText("");

      await apiPost(`/chats/${chatId}/messages`, { text: t });
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setSending(false);
    }
  }

  async function createDirect() {
    const uid = window.prompt("ID сотрудника для личного чата (например DEMO-MASTER):");
    if (!uid) return;
    try { const r = await apiPost<any>("/chats/direct", { user_id: uid.trim() }); await loadMeAndChats(); setActiveId(r.id); }
    catch (e:any) { setError(e?.message || String(e)); }
  }

  async function createGroup() {
    const title = window.prompt("Название группы:"); if (!title) return;
    const raw = window.prompt("ID участников через запятую:", "DEMO-MASTER,DEMO-ENGINEER") || "";
    const member_ids = raw.split(",").map(x => x.trim()).filter(Boolean);
    try { const r = await apiPost<any>("/chats/group", { title, member_ids }); await loadMeAndChats(); setActiveId(r.id); }
    catch (e:any) { setError(e?.message || String(e)); }
  }

  async function createUser() {
    if (!me?.perms?.includes("admin.manage")) return;
    const id=window.prompt("Новый User ID:"); if(!id) return;
    const full_name=window.prompt("ФИО:") || id; const password=window.prompt("Временный пароль:") || "1234";
    const role_key=(window.prompt("Роль WORKER / MASTER / ENGINEER / HEAD / ADMIN:","WORKER")||"WORKER").toUpperCase();
    try { await apiPost("/admin/users", {id,full_name,password,role_key,department_id:me.department_id,section_id:me.section_id}); alert("Пользователь создан"); }
    catch(e:any){ setError(e?.message || String(e)); }
  }

  async function globalSearch() {
    const term=window.prompt("Поиск по сообщениям:"); if(!term || term.trim().length<2) return;
    try { const hits=await apiGet<any[]>(`/search/messages?q=${encodeURIComponent(term.trim())}`);
      if(!hits.length){ alert("Ничего не найдено"); return; }
      const preview=hits.slice(0,10).map((h,i)=>`${i+1}. ${h.chat_title}: ${h.text}`).join("\n");
      const n=Number(window.prompt(`Найдено ${hits.length}. Выбери номер:\n${preview}`,"1"));
      if(n>=1 && n<=Math.min(hits.length,10)) setActiveId(hits[n-1].chat_id);
    } catch(e:any){ setError(e?.message || String(e)); }
  }

  async function onFilePicked(file?: File) {
    if(!file || !activeId) return;
    try { setSending(true); await apiUpload(`/chats/${activeId}/upload`, file); await loadChat(activeId); }
    catch(e:any){ setError(e?.message || String(e)); } finally { setSending(false); if(fileRef.current) fileRef.current.value=""; }
  }

  function humanSize(n:number){ if(n<1024) return `${n} B`; if(n<1048576) return `${(n/1024).toFixed(1)} KB`; return `${(n/1048576).toFixed(1)} MB`; }

  async function toggleVoice() {
    if (recording) { recorderRef.current?.stop(); return; }
    if (!activeId) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream); audioChunksRef.current = []; recorderRef.current = rec;
      rec.ondataavailable = (e) => { if (e.data.size) audioChunksRef.current.push(e.data); };
      rec.onstop = async () => {
        setRecording(false); stream.getTracks().forEach(t => t.stop());
        try {
          const blob = new Blob(audioChunksRef.current, { type: rec.mimeType || "audio/webm" });
          const file = new File([blob], `voice_${Date.now()}.webm`, { type: blob.type });
          setSending(true); await apiUpload(`/chats/${activeId}/upload`, file, "🎤 Голосовое сообщение"); await loadChat(activeId);
        } catch(e:any) { setError(e?.message || String(e)); } finally { setSending(false); }
      };
      rec.start(); setRecording(true);
    } catch(e:any) { setError("Нет доступа к микрофону: " + (e?.message || String(e))); }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  // typing: отправляем событие в WS (не спамим)
  function sendTyping() {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({ type: "typing" }));
    } catch {}
  }

  function onChangeText(v: string) {
    setText(v);
    if (typingTimerRef.current) window.clearTimeout(typingTimerRef.current);
    typingTimerRef.current = window.setTimeout(() => sendTyping(), 400);
  }

  // 0) mobile listener
  useEffect(() => {
    const onResize = () => setMobile(isMobileWidth());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // 1) стартовая загрузка
  useEffect(() => {
    loadMeAndChats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 2) при смене чата: грузим историю + переподключаем WS
  useEffect(() => {
    if (!activeId) return;

    shouldAutoScrollRef.current = true;

    // закрыть старый WS
    try {
      wsRef.current?.close();
    } catch {}
    wsRef.current = null;

    // чистим typing на смене чата
    setTypingUsers({});

    // грузим историю
    loadChat(activeId);

    // ✅ WS URL: из API_BASE вычислим host
    // API_BASE может быть: http://IP:8000/api/v1
    // или: /api/v1 (в браузере)
    const tkn = getToken();

    let wsUrl = "";
    if (API_BASE.startsWith("http")) {
      const u = new URL(API_BASE);
      const proto = u.protocol === "https:" ? "wss" : "ws";
      wsUrl = `${proto}://${u.host}/api/v1/ws/chat/${activeId}?token=${encodeURIComponent(tkn)}`;
    } else {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      wsUrl = `${proto}://${window.location.host}/api/v1/ws/chat/${activeId}?token=${encodeURIComponent(tkn)}`;
    }

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);

        // message
        if (data?.type === "message" && data?.chat_id === activeId && data?.message) {
          const msg = data.message as MsgOut;

          setMessages((prev) => {
            if (msg.id && prev.some((x) => x.id === msg.id)) return prev;

            const mineId = me?.id || "";
            const cleaned =
              msg.user_id === mineId
                ? prev.filter((x) => !(typeof x.id === "number" && x.id < 0 && x.user_id === mineId && x.text === msg.text))
                : prev;

            return [...cleaned, msg];
          });

          if (me?.id && msg.user_id === me.id && typeof msg.id === "number" && msg.id > 0) {
            setSeenMyIds((prev) => {
              const next = new Set(prev);
              next.add(msg.id!);
              return next;
            });
          }

          shouldAutoScrollRef.current = true;
        }

        // typing
        if (data?.type === "typing" && data?.chat_id === activeId && data?.user_id) {
          const uid = String(data.user_id);
          if (me?.id && uid === me.id) return;
          setTypingUsers((prev) => ({ ...prev, [uid]: Date.now() }));
        }
      } catch {}
    };

    return () => {
      try {
        ws.close();
      } catch {}
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, me?.id]);

  // 3) чистим typing каждые 500мс
  useEffect(() => {
    const t = window.setInterval(() => {
      const now = Date.now();
      setTypingUsers((prev) => {
        const next: Record<string, number> = {};
        for (const k of Object.keys(prev)) {
          if (now - prev[k] < 2200) next[k] = prev[k];
        }
        return next;
      });
    }, 500);
    return () => window.clearInterval(t);
  }, []);

  // 4) автоскролл как Telegram: только если пользователь у низа
  useEffect(() => {
    const el = msgsRef.current;
    if (!el) return;
    if (shouldAutoScrollRef.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  function onScrollMessages() {
    const el = msgsRef.current;
    if (!el) return;
    const delta = el.scrollHeight - el.scrollTop - el.clientHeight;
    shouldAutoScrollRef.current = delta < 120;
  }

  const typingList = useMemo(() => Object.keys(typingUsers), [typingUsers]);
  const typingText = useMemo(() => {
    if (!typingList.length) return "";
    if (typingList.length === 1) return `${typingList[0]} печатает…`;
    return `${typingList.slice(0, 2).join(", ")} печатают…`;
  }, [typingList]);

  function selectChat(id: number) {
    setActiveId(id);
    if (mobile) setDrawerOpen(false);
  }

  return (
    <div className="mg-wrap">
      <div className="mg-topbar">
        <div className="mg-brand">
          <div className="mg-title-row">
            {mobile && (
              <button className="btn btn-ghost" onClick={() => setDrawerOpen(true)} aria-label="Open chats">
                ☰
              </button>
            )}
            <h1>Mini-Gramm — Live Chat</h1>
          </div>

          <p>
            {me ? (
              <>
                user: <b>{me.id}</b> • role: <b>{me.role_key}</b> • dept: <b>{me.department_id}</b> • section:{" "}
                <b>{me.section_id}</b>
              </>
            ) : (
              "loading..."
            )}
          </p>
        </div>

        <div className="mg-actions">
          <button className="btn btn-ghost" onClick={createDirect}>+ Личный</button>
          <button className="btn btn-ghost" onClick={createGroup}>+ Группа</button>
          <button className="btn btn-ghost" onClick={globalSearch}>🔎 Сообщения</button>
          {me?.perms?.includes("admin.manage") && <button className="btn btn-ghost" onClick={createUser}>+ Пользователь</button>}
          <button className="btn btn-primary" onClick={loadMeAndChats} disabled={loadingChats}>
            {loadingChats ? "Loading..." : "Reload"}
          </button>
          <button
            className="btn btn-danger"
            onClick={() => {
              localStorage.removeItem("mg_token");
              window.location.reload();
            }}
          >
            Logout
          </button>
        </div>
      </div>

      {/* ✅ Mobile drawer */}
      {mobile && drawerOpen && <div className="mg-backdrop" onClick={() => setDrawerOpen(false)} />}
      {mobile && (
        <div className={"mg-drawer " + (drawerOpen ? "open" : "")}>
          <div className="chats-head">
            <div className="title">Чаты</div>
            <div className="badge">{chats.length}</div>
            <button className="btn btn-ghost" onClick={() => setDrawerOpen(false)} style={{ marginLeft: "auto" }}>
              ✕
            </button>
          </div>

          <div style={{ padding: "0 14px 12px 14px" }}>
            <input className="search" placeholder="Поиск чата..." value={q} onChange={(e) => setQ(e.target.value)} />
          </div>

          <div className="chats-list">
            {filtered.map((c) => {
              const active = c.id === activeId;
              const icon = c.title?.includes("Объяв") ? "📢" : c.title?.includes("SUPER") ? "🧠" : "💬";
              return (
                <div key={c.id} className={"chat-item " + (active ? "active" : "")} onClick={() => selectChat(c.id)}>
                  <div className="chat-ico">{icon}</div>
                  <div className="chat-meta">
                    <div className="chat-title">{c.title} {!!c.unread_count && <span className="badge" style={{marginLeft:8}}>{c.unread_count}</span>}</div>
                    <div className="chat-sub">
                      {c.department_id} / {c.section_id} • id={c.id}
                    </div>
                  </div>
                </div>
              );
            })}
            {!filtered.length && <div style={{ color: "rgba(255,255,255,0.55)", padding: 10 }}>Нет чатов</div>}
          </div>
        </div>
      )}

      <div className={"mg-grid " + (mobile ? "mobile" : "")}>
        {/* LEFT desktop */}
        {!mobile && (
          <div className="panel">
            <div className="chats-head">
              <div className="title">Чаты</div>
              <div className="badge">{chats.length}</div>
            </div>

            <div style={{ padding: "0 14px 12px 14px" }}>
              <input className="search" placeholder="Поиск чата..." value={q} onChange={(e) => setQ(e.target.value)} />
            </div>

            <div className="chats-list">
              {filtered.map((c) => {
                const active = c.id === activeId;
                const icon = c.title?.includes("Объяв") ? "📢" : c.title?.includes("SUPER") ? "🧠" : "💬";
                return (
                  <div key={c.id} className={"chat-item " + (active ? "active" : "")} onClick={() => setActiveId(c.id)}>
                    <div className="chat-ico">{icon}</div>
                    <div className="chat-meta">
                      <div className="chat-title">{c.title} {!!c.unread_count && <span className="badge" style={{marginLeft:8}}>{c.unread_count}</span>}</div>
                      <div className="chat-sub">
                        {c.department_id} / {c.section_id} • id={c.id}
                      </div>
                    </div>
                  </div>
                );
              })}
              {!filtered.length && <div style={{ color: "rgba(255,255,255,0.55)", padding: 10 }}>Нет чатов</div>}
            </div>
          </div>
        )}

        {/* RIGHT */}
        <div className="panel">
          <div className="chat-head">
            <div className="left">
              <div className="chat-ico">{activeChat?.title?.includes("Объяв") ? "📢" : "💬"}</div>
              <div>
                <div style={{ fontWeight: 900 }}>{activeChat?.title || "..."}</div>
                <div className="badge">
                  {activeChat ? `${activeChat.department_id} / ${activeChat.section_id} • id=${activeChat.id}` : ""}
                </div>
              </div>
            </div>
            {readOnly && <div className="lock">🔒 read-only</div>}
          </div>

          <div className="msgs" ref={msgsRef} onScroll={onScrollMessages}>
            {loadingChat && <div style={{ color: "rgba(255,255,255,0.6)" }}>Загрузка…</div>}

            {!loadingChat && messages.length === 0 && (
              <div style={{ color: "rgba(255,255,255,0.6)" }}>Сообщений пока нет. Напиши первое 🙂</div>
            )}

            {messages.map((m, idx) => {
              const mine = me?.id && m.user_id === me.id;
              const isSeen = mine && typeof m.id === "number" && m.id > 0 && seenMyIds.has(m.id);

              return (
                <div key={m.id ?? idx} className={"row " + (mine ? "mine" : "theirs")}>
                  {!mine && <div className="avatar">{initials(m.user_id)}</div>}

                  <div className={"bubble " + (mine ? "mine" : "theirs")}>
                    <div className="meta">
                      <div className="author">{mine ? "Вы" : m.user_id}</div>

                      <div className="tg-right-meta">
                        <div className="time" title={fmtDateTime(m.created_at)}>
                          {fmtTime(m.created_at)}
                        </div>

                        {mine && <div className={"tg-check " + (isSeen ? "seen" : "sent")}>{isSeen ? "✓✓" : "✓"}</div>}
                      </div>
                    </div>

                    <div className="text">{m.text}</div>
                    {!!m.attachments?.length && <div style={{display:"grid",gap:6,marginTop:8}}>
                      {m.attachments.map(a => <button key={a.id} className="btn btn-ghost" onClick={() => downloadFile(a.url,a.name)}>📎 {a.name} · {humanSize(a.size_bytes)}</button>)}
                    </div>}
                  </div>

                  {mine && <div className="avatar">{initials(me?.id || "U")}</div>}
                </div>
              );
            })}
          </div>

          {typingText && <div className="typing-indicator">{typingText}</div>}

          <div className="composer">
            <input ref={fileRef} type="file" style={{display:"none"}} onChange={(e)=>onFilePicked(e.target.files?.[0])} />
            <button className="btn btn-ghost" onClick={()=>fileRef.current?.click()} disabled={readOnly || !activeId || sending}>📎</button>
            <button className={"btn " + (recording ? "btn-danger" : "btn-ghost")} onClick={toggleVoice} disabled={readOnly || !activeId || sending}>{recording ? "■ Стоп" : "🎤"}</button>
            <textarea
              value={text}
              onChange={(e) => onChangeText(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={readOnly ? "Этот чат только для чтения" : "Напиши сообщение…"}
              disabled={readOnly || !activeId}
            />
            <button className="btn btn-primary" onClick={send} disabled={sending || readOnly || !activeId}>
              {sending ? "..." : "Send"}
            </button>
          </div>

          <div className="small">Enter — отправить, Shift+Enter — новая строка</div>
        </div>
      </div>

      {error && <div className="toast">⚠ {error}</div>}
    </div>
  );
}
