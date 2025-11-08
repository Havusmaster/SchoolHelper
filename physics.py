import re

def solve_physics(task_text):
    text = task_text.lower().strip()
    numbers = [float(n) for n in re.findall(r'\d+\.?\d*', text)]

    if 'скорость' in text and len(numbers) >= 2:
        distance, time = numbers[:2]
        speed = distance / time
        return f"Скорость: {distance} / {time} = {speed}", speed

    elif 'работа' in text and len(numbers) >= 2:
        force, distance = numbers[:2]
        work = force * distance
        return f"Работа: {force} × {distance} = {work}", work

    elif 'сила' in text and len(numbers) >= 2:
        mass, acceleration = numbers[:2]
        force = mass * acceleration
        return f"Сила: {mass} × {acceleration} = {force}", force

    else:
        return "Не понял задачу по физике. Пример: 'скорость 100 2'", None