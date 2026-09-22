"""Board-only cache repair for CLI isolated profiles when GitHub is unreachable.

Matches Arduino CLI cores.ToolDependency.InternalUniqueIdentifier; references the
already installed exact tool version instead of downloading a duplicate.
"""
from pathlib import Path
import hashlib
import subprocess

base=Path.home()/'.arduino15'
dependency='zephyr:arm-zephyr-eabi@1.0.1'
uid=dependency.replace(':','_').replace('@','_')+'_'+hashlib.sha256(dependency.encode()).hexdigest()[:16]
source=base/'packages/zephyr/tools/arm-zephyr-eabi/1.0.1'
subprocess.run([str(source/'bin/arm-zephyr-eabi-g++'),'--version'],check=True)
target=base/'internal'/uid
if not target.exists(): target.symlink_to(source,target_is_directory=True)
print(target,'->',target.resolve())
