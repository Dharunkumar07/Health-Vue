import { useCallback, useState } from 'react';

let idSeq = 0;

export default function useToast() {
  const [toasts, setToasts] = useState([]);

  const show = useCallback((message, duration = 2600) => {
    const id = ++idSeq;
    setToasts((prev) => [...prev, { id, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, duration);
  }, []);

  return { toasts, show };
}
