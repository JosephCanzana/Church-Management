"""faith.permissions: who may use goals. This is the only place that decides it.

Only people who can sign in take part. Guests and no-account members never hold goals,
so the rule is `User.can_sign_in` (never a fresh test written from `status`).
"""


def can_use_goals(user):
    """True for a signed-in person who can sign in, whatever their role."""
    return bool(user is not None and getattr(user, "is_authenticated", False) and user.can_sign_in)


def can_manage_goal(user, goal):
    """True when `user` may use goals and owns `goal`. Goals are private: only the owner."""
    return can_use_goals(user) and goal.user_id == user.pk
