You are documenting legacy Oracle PL/SQL for a team that will rebuild
it as a REST service. Given the signature and full body of one
PL/SQL unit, write a plain-English spec a backend engineer with no
PL/SQL background could use to reimplement it correctly.

Cover, in this order:
1. **Purpose** -- one sentence, what business action this performs.
2. **Inputs** -- each parameter, its role, and any implicit
   constraints visible in the code (e.g. "must be an existing
   emp_id" if the code does a lookup and raises on no match).
3. **Side effects** -- every INSERT/UPDATE/DELETE, every other unit
   called, every COMMIT/ROLLBACK, and anything written to
   package-level state.
4. **Business rules** -- any conditional logic that encodes a policy
   (thresholds, freezes, validation), stated as a rule, not as code.
5. **Error behavior** -- what causes a failure, and what happens to
   already-made changes when it fails (rolled back? already committed
   via autonomous transaction?).

Be concrete and specific to this code -- do not pad with generic
PL/SQL background. If something in the code looks unsafe to migrate
mechanically (dynamic SQL, autonomous transactions, package state),
say so plainly in the "Side effects" section, but leave the formal
risk classification to the separate risk report -- your job here is
the spec, not the risk verdict.

---

Unit: {{ unit_type }} {{ unit_name }}
Package: {{ package_name }}

Parameters:
{{ params_block }}

{% if return_type %}Returns: {{ return_type }}{% endif %}

Body:
```sql
{{ body }}
```
