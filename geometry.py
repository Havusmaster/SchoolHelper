import re
import math

def solve_geometry(task_text):
    text = task_text.lower().strip()
    numbers = [float(n) for n in re.findall(r'\d+\.?\d*', text)]

    if 'площадь треугольника' in text and len(numbers) >= 2:
        base, height = numbers[:2]
        area = 0.5 * base * height
        return f"Площадь треугольника: ½ × {base} × {height} = {area}", area

    elif 'площадь круга' in text and len(numbers) >= 1:
        radius = numbers[0]
        area = math.pi * radius ** 2
        return f"Площадь круга: π × {radius}² = {area:.2f}", area

    elif 'площадь прямоугольника' in text and len(numbers) >= 2:
        length, width = numbers[:2]
        area = length * width
        return f"Площадь прямоугольника: {length} × {width} = {area}", area

    elif 'периметр' in text and len(numbers) >= 2:
        sides = numbers
        perimeter = sum(sides)
        return f"Периметр: { ' + '.join(map(str, sides)) } = {perimeter}", perimeter

    elif 'пифагор' in text and len(numbers) >= 2:
        a, b = numbers[:2]
        c = math.sqrt(a**2 + b**2)
        return f"Теорема Пифагора: √({a}² + {b}²) = {c:.2f}", c

    else:
        return "Не понял задачу по геометрии. Пример: 'площадь треугольника 6 4'", None