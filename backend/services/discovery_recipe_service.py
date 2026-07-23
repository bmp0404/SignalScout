"""DiscoveryRecipeService: list/run/dry-run/approve recipes and summarize cost.

Orchestration only — every search, dedupe, and credit spend happens inside
ProviderExpander/ProviderBudget/provider_search_checkpoints. This service adds
no parallel dedupe ledger or budget table.
"""

from datetime import datetime, timedelta, timezone

from backend.db.repositories.discovery_recipes import DiscoveryRecipeRepository
from backend.db.repositories.enrichment import EnrichmentUsageRepository
from backend.db.repositories.persons import PersonRepository
from backend.db.repositories.provider_identities import ProviderIdentityRepository
from backend.discovery.provider_expansion import ProviderExpander
from backend.domain.discovery_recipe import DiscoveryRecipe
from backend.enrichment.budgets import SEARCH, ProviderBudget

FREQUENCY_INTERVALS = {
    "weekly": timedelta(days=7),
    "biweekly": timedelta(days=14),
}


class DiscoveryRecipeService:
    def __init__(
        self,
        recipes: DiscoveryRecipeRepository,
        identities: ProviderIdentityRepository,
        expander: ProviderExpander,
        budget: ProviderBudget,
        usage: EnrichmentUsageRepository,
        persons: PersonRepository,
        candidate_service=None,
    ):
        self.recipes = recipes
        self.identities = identities
        self.expander = expander
        self.budget = budget
        self.usage = usage
        self.persons = persons
        self.candidate_service = candidate_service

    def list_recipes(self) -> list[dict]:
        return [self._recipe_row(recipe) for recipe in self.recipes.all()]

    def approve(self, recipe_id: str) -> dict:
        self._get(recipe_id)
        self.recipes.set_approval_state(recipe_id, "approved")
        return self._recipe_row(self._get(recipe_id))

    def run(self, recipe_id: str, override_limit: int | None = None) -> dict:
        return self._run(recipe_id, dry_run=False, override_limit=override_limit)

    def dry_run(self, recipe_id: str, override_limit: int | None = None) -> dict:
        return self._run(recipe_id, dry_run=True, override_limit=override_limit)

    def is_due(self, recipe: DiscoveryRecipe, now: datetime | None = None) -> bool:
        """True when an active, approved, non-manual recipe's interval has elapsed."""
        if recipe.status != "active":
            return False
        if recipe.approval_state != "approved":
            return False
        interval = FREQUENCY_INTERVALS.get(recipe.frequency)
        if interval is None:
            return False
        run_at = now or datetime.now(timezone.utc)
        if not recipe.last_run:
            return True
        try:
            last = datetime.fromisoformat(recipe.last_run)
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return run_at - last >= interval

    def run_due(self, now: datetime | None = None) -> dict:
        """Run every due recipe (active + approved + frequency elapsed). Skips failures."""
        run_at = now or datetime.now(timezone.utc)
        due = [recipe for recipe in self.recipes.all() if self.is_due(recipe, run_at)]
        results = []
        created_total = 0
        for recipe in due:
            try:
                summary = self._run(recipe.id, dry_run=False, override_limit=None)
                results.append({"recipe_id": recipe.id, "status": "ran", **summary})
                created_total += int(summary.get("created") or 0)
            except Exception as exc:  # noqa: BLE001 — cron must continue across recipes
                results.append({
                    "recipe_id": recipe.id,
                    "status": "error",
                    "error": str(exc),
                })
        if created_total and self.candidate_service is not None:
            self.candidate_service.rescore_all()
        return {
            "run_at": run_at.isoformat(timespec="seconds"),
            "due_count": len(due),
            "ran_count": sum(1 for row in results if row["status"] == "ran"),
            "error_count": sum(1 for row in results if row["status"] == "error"),
            "created_total": created_total,
            "results": results,
        }

    def cost_summary(self) -> dict:
        today = datetime.now(timezone.utc).date()
        month = today.strftime("%Y-%m")
        providers = {recipe.provider for recipe in self.recipes.all()}
        provider_totals = {}
        for provider in providers:
            search_used = (
                self.usage.count_for_month(provider, month, SEARCH)
                if provider == "pdl"
                else self.usage.count_for(provider, today.isoformat(), SEARCH)
            )
            provider_totals[provider] = {
                "search_credits_used": search_used,
                "search_credits_remaining": self.budget.remaining(provider, SEARCH),
            }
        duplicates_skipped = 0
        credits_saved = 0
        recipe_totals = []
        for recipe in self.recipes.all():
            checkpoint = self.expander.recipe_checkpoint(recipe)
            if checkpoint is None:
                continue
            created = checkpoint.verified_count + checkpoint.review_count
            recipe_totals.append({
                "recipe_id": recipe.id,
                "provider": recipe.provider,
                "credit_units": checkpoint.credit_units,
                # Coresignal only: Search and Collect are billed separately.
                # Providers that don't distinguish (PDL) leave both 0.
                "search_credit_units": checkpoint.search_credit_units,
                "collect_credit_units": checkpoint.collect_credit_units,
                "created": created,
                "duplicate_count": checkpoint.duplicate_count,
                "merged_count": checkpoint.merged_count,
            })
            duplicates_skipped += checkpoint.duplicate_count + checkpoint.merged_count
            # Every candidate stored via provider search arrives fully enriched —
            # each one is an ENRICH-lane credit that was never spent.
            credits_saved += created
        return {
            "provider_totals": provider_totals,
            "recipe_totals": recipe_totals,
            "duplicates_skipped": duplicates_skipped,
            "enrichment_credits_saved": credits_saved,
            "candidates_by_discovery_source": self._candidates_by_discovery_source(),
        }

    def _run(self, recipe_id: str, dry_run: bool, override_limit: int | None) -> dict:
        recipe = self._get(recipe_id)
        result = self.expander.run_recipe(recipe, dry_run=dry_run, override_limit=override_limit)
        if not dry_run:
            self.recipes.set_last_run(
                recipe_id, datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
        return {
            "recipe_id": recipe_id,
            "provider": recipe.provider,
            "dry_run": dry_run,
            "attempted": result.attempted,
            "returned_records": result.returned_records,
            "created": len(result.created),
            "verified": result.verified,
            "review": result.review,
            "merged": result.merged,
            "duplicates": result.duplicates,
            "rejected": result.rejected,
            "rejection_reasons": result.rejection_reasons,
            "credit_units": result.credit_units,
            "planned_pages": result.planned_pages,
        }

    def _recipe_row(self, recipe: DiscoveryRecipe) -> dict:
        checkpoint = self.expander.recipe_checkpoint(recipe)
        return {
            "id": recipe.id,
            "name": recipe.name,
            "provider": recipe.provider,
            "query_type": recipe.query_type,
            "default_limit": recipe.default_limit,
            "frequency": recipe.frequency,
            "status": recipe.status,
            "approval_state": recipe.approval_state,
            "last_run": recipe.last_run,
            "last_result_count": checkpoint.returned_records if checkpoint else 0,
            "last_created_count": (
                checkpoint.verified_count + checkpoint.review_count if checkpoint else 0
            ),
            "last_duplicate_count": checkpoint.duplicate_count if checkpoint else 0,
            "last_credit_units": checkpoint.credit_units if checkpoint else 0,
            "last_outcome": checkpoint.last_outcome if checkpoint else "never_run",
            "due": self.is_due(recipe),
        }

    def _get(self, recipe_id: str) -> DiscoveryRecipe:
        recipe = self.recipes.get(recipe_id)
        if recipe is None:
            raise ValueError(f"unknown recipe {recipe_id!r}")
        return recipe

    def _candidates_by_discovery_source(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for person in self.persons.all("discovery"):
            source = person.discovery_source or "unspecified"
            counts[source] = counts.get(source, 0) + 1
        return counts
