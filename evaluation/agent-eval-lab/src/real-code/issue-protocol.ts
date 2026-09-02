export function isProviderInfrastructureError(message: string): boolean {
	return /connection|connect|network|socket|fetch failed|timed?\s*out|timeout|rate.?limit|too many requests|429|401|403|authentication|api.?key|service unavailable|bad gateway|gateway timeout|\b5\d\d\b/i.test(
		message,
	);
}
