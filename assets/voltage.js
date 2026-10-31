let voltageRequestBusy = false;
function showSupplyVoltage(value) {
  const valid = value?.available && Number.isFinite(value.volts);
  const button = document.getElementById('read-voltage');
  document.getElementById('supply-voltage').textContent = valid ? value.volts.toFixed(1)+' V' : '—';
  const readTime = Number.isFinite(value?.read_at_ms) && value.read_at_ms > 0
    ? new Date(value.read_at_ms).toLocaleString(document.documentElement?.lang || 'en', {
        year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit',
        minute:'2-digit', second:'2-digit', hourCycle:'h23', timeZoneName:'short'
      })
    : 'Time unavailable';
  document.getElementById('supply-voltage-detail').textContent = value?.pending
    ? `Reading servo ${value.servo_id}…`
    : value?.error || (valid ? `ID ${value.servo_id} · Last read · ${readTime}${value.connected ? '' : ' · Disconnected'}`
      : value?.connected ? 'Not read yet · Click to read' : 'Robot disconnected');
  button.disabled = voltageRequestBusy || !value?.connected || value.pending || value.retry_after_ms > 0;
  button.textContent = voltageRequestBusy || value?.pending ? 'Reading…' : 'Read voltage';
}
document.getElementById('read-voltage').addEventListener('click', async () => {
  if (voltageRequestBusy) return;
  const input = document.getElementById('voltage-servo-id');
  const servoId = Number(input.value);
  if (!input.value.trim() || !Number.isInteger(servoId) || servoId < 0 || servoId > 253) {
    document.getElementById('supply-voltage-detail').textContent = 'Servo ID must be an integer between 0 and 253';
    return;
  }
  voltageRequestBusy = true;
  const button = document.getElementById('read-voltage');
  button.disabled = true;
  button.textContent = 'Reading…';
  try {
    const response = await fetch('/api/voltage/read', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({servo_id:servoId}),
      signal:AbortSignal.timeout(2500)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Read request failed');
  } catch (error) {
    document.getElementById('supply-voltage-detail').textContent = error.message;
  } finally {
    voltageRequestBusy = false;
    // The regular status refresh owns the pending/result display and re-enables the button.
  }
});
