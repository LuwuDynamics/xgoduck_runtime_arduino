#!/bin/bash
# Run against the real arm64 package in a disposable container, without board hardware.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
docker run --rm -i --platform linux/arm64 --network none --user 0 \
  -v "$root/tools:/tools:ro" --entrypoint /bin/bash \
  ghcr.io/arduino/app-bricks/python-apps-base:0.13.2 -se <<'CONTAINER'
mkdir -p /tmp/fake
export PATH=/tmp/fake:$PATH
target=/etc/systemd/system/arduino-router.service.d/90-xgoduck-baud.conf
cat > /tmp/fake/systemctl <<'MOCK'
#!/bin/bash
set -eu
conf=/etc/systemd/system/arduino-router.service.d/90-xgoduck-baud.conf
case $1 in
cat|daemon-reload) ;;
show)
  if [[ $5 == --value && $4 == ExecStart ]]; then
    if [[ -f /tmp/override ]]; then cat /tmp/override; exit 0; fi
    command=$(sed -n 's/^ExecStart=\(.\)/\1/p' "$conf")
    echo "{ path=${command%% *} ; argv[]=$command ; }"
  else
    cat /tmp/router.pid
  fi ;;
is-active) [[ -f /tmp/router.pid ]] && kill -0 "$(cat /tmp/router.pid)" ;;
restart)
  echo restart >> /tmp/events
  if [[ -f /tmp/fail-once ]]; then rm /tmp/fail-once; exit 1; fi
  if [[ -f /tmp/router.pid ]]; then kill "$(cat /tmp/router.pid)" || true; fi
  /usr/local/lib/xgoduck-router-rc/arduino-router-45cccf1 --unix-port /tmp/test.sock \
    --monitor-port '' >/tmp/router.log 2>&1 9>&- &
  echo $! > /tmp/router.pid ;;
stop) rm -f /tmp/router.pid ;;
*) exit 2 ;;
esac
MOCK
chmod +x /tmp/fake/systemctl
configure() { bash /tools/router-2m.sh; }
restarts() { wc -l </tmp/events; }

configure
[[ $(restarts) == 1 ]]
grep -q -- '--serial-baudrate 115200 --serial-max-baudrate 2000000 --serial-protocol auto' "$target"
configure
[[ $(restarts) == 1 ]]

printf '[Service]\nExecStart=\nExecStart=/usr/bin/arduino-router --serial-baudrate 2000000\n' >/tmp/fixed
cp /tmp/fixed "$target"
configure
[[ $(restarts) == 2 ]]
cmp /tmp/fixed /var/backups/xgoduck-router/90-xgoduck-baud-*.conf
configure
[[ $(restarts) == 2 ]]

cp /tmp/fixed "$target"
echo '{ path=/custom/router ; }' >/tmp/override
if configure; then exit 1; fi
cmp /tmp/fixed "$target"
[[ $(restarts) == 2 ]]
rm /tmp/override
mkdir -p /tmp/damaged/router-runtime
cp /tools/router-2m.sh /tmp/damaged/
touch /tmp/damaged/router-runtime/arduino-router_0.0.0-45cccf1-1_arm64.deb
if bash /tmp/damaged/router-2m.sh; then exit 1; fi
cmp /tmp/fixed "$target"
[[ $(restarts) == 2 ]]

touch /tmp/fail-once
if configure; then exit 1; fi
cmp /tmp/fixed "$target"
[[ $(restarts) == 4 ]]
rm "$target" /tmp/router.pid
touch /tmp/fail-once
if configure; then exit 1; fi
[[ ! -e $target && ! -e /tmp/router.pid ]]
echo 'PASS: fresh install, fixed-2M migration, repeat installs, damaged package, conflicting override, and rollback.'
CONTAINER
