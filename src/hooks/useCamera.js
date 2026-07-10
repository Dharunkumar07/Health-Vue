import { useCallback, useEffect, useRef, useState } from 'react';

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function useCamera() {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const recordStartRef = useRef(0);

  const [status, setStatus] = useState('requesting'); // requesting | live | error
  const [error, setError] = useState('');
  const [frameRate, setFrameRate] = useState(null);
  const [recording, setRecording] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);

  // Acquire the first available camera on mount (external USB/UVC camera or built-in).
  useEffect(() => {
    let cancelled = false;
    setStatus('requesting');

    async function start() {
      if (!navigator.mediaDevices?.getUserMedia) {
        if (!cancelled) {
          setStatus('error');
          setError('Camera API not available in this browser');
        }
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 1920 }, height: { ideal: 1080 } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) videoRef.current.srcObject = stream;

        const settings = stream.getVideoTracks()[0]?.getSettings?.() || {};
        setFrameRate(settings.frameRate ? Math.round(settings.frameRate) : null);
        setStatus('live');
        setError('');
      } catch (e) {
        if (!cancelled) {
          setStatus('error');
          setError(e.message || 'Camera permission denied');
        }
      }
    }

    start();
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, []);

  const snapshotBlob = useCallback(() => {
    const video = videoRef.current;
    if (!video || video.readyState < 2) return Promise.resolve(null);
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 720;
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  }, []);

  const downloadSnapshot = useCallback(async () => {
    const blob = await snapshotBlob();
    if (!blob) return false;
    downloadBlob(blob, `healthvue-snapshot-${Date.now()}.png`);
    return true;
  }, [snapshotBlob]);

  const startRecording = useCallback(() => {
    const stream = streamRef.current;
    if (!stream || status !== 'live' || recording) return;

    const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
      ? 'video/webm;codecs=vp9'
      : 'video/webm';
    const recorder = new MediaRecorder(stream, { mimeType });
    chunksRef.current = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: mimeType });
      downloadBlob(blob, `healthvue-recording-${Date.now()}.webm`);
    };

    recorder.start();
    recorderRef.current = recorder;
    recordStartRef.current = Date.now();
    setRecording(true);
    setElapsedSec(0);
  }, [status, recording]);

  const stopRecording = useCallback(() => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  }, []);

  useEffect(() => {
    if (!recording) return undefined;
    const id = setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - recordStartRef.current) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [recording]);

  return {
    videoRef,
    status,
    error,
    frameRate,
    recording,
    elapsedSec,
    downloadSnapshot,
    startRecording,
    stopRecording,
  };
}
