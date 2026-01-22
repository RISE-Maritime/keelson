"""Legacy utilities module - deprecated.

This module is deprecated. Please use keelson.scaffolding instead.

Migration:
    # Old import
    from keelson.utils import make_configurable

    # New import
    from keelson.scaffolding import make_configurable
"""

import warnings


def __getattr__(name):
    if name == "make_configurable":
        warnings.warn(
            "keelson.utils is deprecated. Use keelson.scaffolding instead: "
            "from keelson.scaffolding import make_configurable",
            DeprecationWarning,
            stacklevel=2,
        )
        from keelson.scaffolding import make_configurable

        return make_configurable
    raise AttributeError(f"module 'keelson.utils' has no attribute '{name}'")
