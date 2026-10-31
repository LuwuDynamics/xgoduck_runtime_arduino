function showSupplyVoltage(value) {
  const valid=value?.available && Number.isFinite(value.volts);
  document.getElementById('supply-voltage').textContent=valid?value.volts.toFixed(1)+' V':'—';
  document.getElementById('supply-voltage-detail').textContent=valid
    ?`${value.valid_count} 个读数 · ${value.min_volts.toFixed(1)}–${value.max_volts.toFixed(1)} V`
    :'读数不可用或已过期';
}
