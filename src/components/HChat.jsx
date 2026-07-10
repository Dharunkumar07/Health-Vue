import { useEffect, useRef, useState } from 'react';

let uid = 0;
function nextId() {
  uid += 1;
  return uid;
}

export default function HChat({
  name = 'H',
  caption = 'Assistant · on-device',
  initialMessages = [],
  replies = ['On it.'],
  chips = [],
  placeholder = 'Ask H anything…',
  headerExtra = null,
  footer = null,
}) {
  const [messages, setMessages] = useState(() =>
    initialMessages.map((m) => ({ ...m, id: nextId() }))
  );
  const [value, setValue] = useState('');
  const scrollRef = useRef(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  function botReply() {
    const pool = replies.length ? replies : ['On it.'];
    const text = pool[Math.floor(Math.random() * pool.length)];
    const typingId = nextId();
    setMessages((prev) => [...prev, { id: typingId, who: 'h', text: '…' }]);
    setTimeout(() => {
      setMessages((prev) =>
        prev.map((m) => (m.id === typingId ? { ...m, text } : m))
      );
    }, 650);
  }

  function ask(text) {
    setMessages((prev) => [...prev, { id: nextId(), who: 'u', text }]);
    botReply();
  }

  function send() {
    const v = value.trim();
    if (!v) return;
    setMessages((prev) => [...prev, { id: nextId(), who: 'u', text: v }]);
    setValue('');
    botReply();
  }

  return (
    <div className="hchat">
      <div className="hchat-head">
        <div className="h-avatar">
          <div className="cell"></div>
        </div>
        <div className="h-name">
          {name}
          <small>{caption}</small>
        </div>
        {headerExtra}
      </div>
      <div className="chat-scroll" ref={scrollRef}>
        {messages.map((m) => (
          <div className={'msg ' + m.who} key={m.id}>
            {m.text}
            {m.cite && <span className="cite">{m.cite}</span>}
          </div>
        ))}
      </div>
      {chips.length > 0 && (
        <div className="chips">
          {chips.map((c) => (
            <button className="chip" key={c.label} onClick={() => ask(c.text)}>
              {c.label}
            </button>
          ))}
        </div>
      )}
      <div className="chat-input">
        <input
          value={value}
          placeholder={placeholder}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') send();
          }}
        />
        <button onClick={send}>
          <svg viewBox="0 0 24 24">
            <path d="M4 12h16M14 6l6 6-6 6" />
          </svg>
        </button>
      </div>
      {footer}
    </div>
  );
}
