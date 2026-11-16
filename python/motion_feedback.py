"""Readiness for the joints an action uses; no hardware access or commands."""
import math

LEG_IDS = (10, 11, 12, 13, 14, 20, 21, 22, 23, 24)
HEAD_IDS = (30, 31, 32, 33)
MOUTH_ID = 34
SERVO_IDS = LEG_IDS + HEAD_IDS + (MOUTH_ID,)


def feedback_ready(status, required_ids=SERVO_IDS, *, allow_timeout_bypass=True):
    """An unrelated absent servo must not make healthy joints unavailable."""
    full = status.get('full_robot_ready') is True
    bypass = allow_timeout_bypass and status.get('timeout_checks_disabled') is True
    if not bypass and status.get('imu_ok') is not True and not full:
        return False
    if not bypass and status.get('imu_ok') is False:
        return False
    if 'feedback_age_ms' in status:
        age = status['feedback_age_ms']
        if (isinstance(age, bool) or not isinstance(age, (int, float))
                or not math.isfinite(age) or age < 0 or (not bypass and age >= 150)):
            return False
    elif not full:
        return False
    present = status.get('servo_ids')
    if isinstance(present, (list, tuple)):
        return all(i in present for i in required_ids)
    missing = status.get('missing_joint_ids')
    if isinstance(missing, (list, tuple)):
        return not any(i in missing for i in required_ids)
    return full  # Older status providers have only the aggregate flag.


def feedback_problem(status, required_ids=SERVO_IDS):
    """Describe observed faults without inventing a physical root cause."""
    if feedback_ready(status, required_ids, allow_timeout_bypass=False):
        return ''
    problems = []
    age = status.get('feedback_age_ms')
    if 'feedback_age_ms' in status and age is None:
        problems.append('尚未收到 STM32 的舵机和传感器反馈')
    elif (not isinstance(age, bool) and isinstance(age, (int, float))
          and math.isfinite(age) and age >= 150):
        problems.append(f'STM32 反馈已 {age:.0f} 毫秒未更新')
    present = status.get('servo_ids')
    if isinstance(present, (list, tuple)):
        missing = [i for i in required_ids if i not in present]
    else:
        known_missing = status.get('missing_joint_ids') or []
        missing = [i for i in required_ids if i in known_missing]
    if missing:
        ids = '、'.join(str(i) for i in missing)
        problems.append(f'{ids} 号舵机无有效位置反馈，具体原因尚未确认')
    if status.get('imu_ok') is False:
        problems.append('IMU 当前没有有效数据')
    if not problems:
        problems.append('所需舵机或传感器反馈尚未就绪，当前数据未提供具体原因')
    if status.get('imu_ok') is True:
        problems.append('IMU 正常')
    return '；'.join(problems) + '。'


def feedback_mask(required_ids):
    return sum(1 << SERVO_IDS.index(i) for i in set(required_ids))
