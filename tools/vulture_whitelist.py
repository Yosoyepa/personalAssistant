"""Whitelist de vulture para el gate G-DEAD.

Este archivo nunca se ejecuta: vulture solo lo parsea y marca como usados los
nombres referenciados. `__exit__` exige el tercer parámetro posicional
(`exc_traceback`) aunque la implementación no lo use — convención de context
managers. Cualquier otro hallazgo de vulture sigue fallando el gate.
"""

exc_traceback = None
_WHITELISTED_NAMES = [exc_traceback]
