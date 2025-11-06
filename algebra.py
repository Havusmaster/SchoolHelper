import re
from sympy import symbols, Eq, solve, simplify, Poly
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application, convert_xor


def solve_equation(equation_text):
    """
    Решает уравнение и возвращает пошаговое решение.
    
    Args:
        equation_text: строка с уравнением (например: "2x + 5 = 13")
    
    Returns:
        tuple: (steps_text, solution) где steps_text - текст с шагами, solution - список корней
    """
    try:
        # Очистка и подготовка для школьных уравнений
        text = re.sub(r'\s+', '', equation_text)
        text = text.lower().replace('х', 'x').replace('ь', '').replace("'", '').replace('"', '').replace('`', '').replace(''', '').replace(''', '')
        text = re.sub(r'([a-z])(\d)', r'\1*\2', text)  # x2 → x*2
        text = re.sub(r'(\d)([a-z])', r'\1*\2', text)  # 2x → 2*x
        text = re.sub(r'[^0-9a-z+\-*/()=.\^]', '', text)
        text = text.replace('^', '**')  # x^2 → x**2 для степеней

        if '=' not in text:
            return "Ошибка: Нет '='. Пример: '2x+5=13'", None
        
        left, right = text.split('=', 1)
        left = left.strip()
        right = right.strip()
        
        if not left or not right:
            return "Ошибка: Пустая сторона.", None
        
        x = symbols('x')
        transformations = standard_transformations + (implicit_multiplication_application, convert_xor,)
        
        left_expr = parse_expr(left, transformations=transformations)
        right_expr = parse_expr(right, transformations=transformations)
        
        eq = Eq(left_expr, right_expr)
        solution = solve(eq, x)
        
        # Шаги для школьников
        steps = []
        steps.append(f"Уравнение: {equation_text}")
        steps.append(f"Очищенное: {left} = {right}")
        
        diff_expr = simplify(left_expr - right_expr)
        steps.append(f"Приведено к виду: {diff_expr} = 0")
        
        try:
            poly = Poly(diff_expr, x)
            degree = poly.degree()
            coeffs = poly.all_coeffs()
            
            if degree == 1:
                a = coeffs[0]
                b = coeffs[1] if len(coeffs) > 1 else 0
                steps.append(f"Линейное уравнение: {a}x {'' if b < 0 else '+'} {b} = 0")
                steps.append(f"Решение: x = -{b} / {a}")
                steps.append(f"x = {solution[0]} ✅")
            
            elif degree == 2:
                a = coeffs[0]
                b = coeffs[1] if len(coeffs) > 1 else 0
                c = coeffs[2] if len(coeffs) > 2 else 0
                steps.append(f"Квадратное уравнение: {a}x² {'' if b < 0 else '+'} {b}x {'' if c < 0 else '+'} {c} = 0")
                disc = simplify(b**2 - 4*a*c)
                steps.append(f"Дискриминант D = b² - 4ac = {disc}")
                if disc > 0:
                    steps.append("Два вещественных корня:")
                    steps.append(f"Формула: x = [-b ± √D] / (2a)")
                    steps.append(f"x1 = {solution[0]}")
                    steps.append(f"x2 = {solution[1]}")
                elif disc == 0:
                    steps.append("Один вещественный корень:")
                    steps.append(f"Формула: x = -b / (2a)")
                    steps.append(f"x = {solution[0]}")
                else:
                    steps.append("<u>Для 9 класса</u>: остановись, нет вещественных корней (D &lt; 0).")
                    steps.append(f"<u>Для 11 класса</u>: комплексные корни: x = [-b ± √D] / (2a), где √D = √({-disc}) * i")
                    if solution:
                        steps.append(f"x1 = {solution[0]}")
                        steps.append(f"x2 = {solution[1]}")
            else:
                steps.append(f"Решение: {solution}")
        
        except:
            steps.append(f"Решение: {solution}")
        
        return '\n'.join(steps), solution
    except Exception as e:
        return f"Ошибка: {str(e)}. Введи вручную, например: 2x + 5 = 13", None

