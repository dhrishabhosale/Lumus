"""
Vendor relationship graph, built with networkx.

Edges:
  BID_ON              vendor -> tender it bid on
  WON                 vendor -> contract it won
  CO_BID_WITH         vendor <-> vendor, both bid on the same tender
  SHARES_DIRECTOR     vendor <-> vendor, same director_name on file
  SHARES_ADDRESS      vendor <-> vendor, same registered address
  SHARES_GSTIN_PREFIX vendor <-> vendor, same GSTIN prefix (catches
                       shell entities registered off one parent filing)

Shell/collusion candidates are found via connected components over just
the SHARES_* edges -- vendors that look independent (different names,
different bid amounts) but collapse into one cluster once shared
registration attributes are considered.
"""

import sqlite3
import networkx as nx


def build_graph(conn: sqlite3.Connection) -> nx.MultiGraph:
    g = nx.MultiGraph()

    vendors = conn.execute(
        "SELECT vendor_id, name, gstin, address, director_name FROM vendors"
    ).fetchall()
    for vendor_id, name, gstin, address, director in vendors:
        g.add_node(vendor_id, name=name, gstin=gstin, address=address, director=director)

    # BID_ON / WON edges
    bids = conn.execute("SELECT tender_id, vendor_id, won FROM bids").fetchall()
    for tender_id, vendor_id, won in bids:
        tnode = f"tender:{tender_id}"
        g.add_node(tnode, kind="tender")
        g.add_edge(vendor_id, tnode, relation="BID_ON")
        if won:
            g.add_edge(vendor_id, tnode, relation="WON")

    # CO_BID_WITH: any two vendors that bid on the same tender
    tender_bidders: dict[str, list[str]] = {}
    for tender_id, vendor_id, _ in bids:
        tender_bidders.setdefault(tender_id, []).append(vendor_id)
    for tender_id, bidders in tender_bidders.items():
        for i in range(len(bidders)):
            for j in range(i + 1, len(bidders)):
                g.add_edge(bidders[i], bidders[j], relation="CO_BID_WITH", tender_id=tender_id)

    # SHARES_* edges: group vendors by shared attribute, connect within groups
    def add_shared_edges(attr_index: int, relation: str, key_fn=lambda x: x):
        groups: dict[str, list[str]] = {}
        for row in vendors:
            vendor_id = row[0]
            key = key_fn(row[attr_index])
            if key:
                groups.setdefault(key, []).append(vendor_id)
        for key, members in groups.items():
            if len(members) > 1:
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        g.add_edge(members[i], members[j], relation=relation)

    add_shared_edges(4, "SHARES_DIRECTOR")               # director_name
    add_shared_edges(3, "SHARES_ADDRESS")                 # address
    add_shared_edges(2, "SHARES_GSTIN_PREFIX", key_fn=lambda gstin: gstin[:7])

    return g


def shell_cluster_candidates(g: nx.MultiGraph, min_cluster_size: int = 2) -> list[set]:
    """Connected components restricted to SHARES_* edges only -- vendors
    linked by shared registration attributes, independent of whether they
    ever co-bid. Returns clusters of size >= min_cluster_size.
    """
    shares_only = nx.MultiGraph()
    shares_only.add_nodes_from(n for n, d in g.nodes(data=True) if d.get("kind") != "tender")
    for u, v, data in g.edges(data=True):
        if data.get("relation", "").startswith("SHARES_"):
            shares_only.add_edge(u, v)

    clusters = [c for c in nx.connected_components(shares_only) if len(c) >= min_cluster_size]
    return clusters


def co_bidding_partners(g: nx.MultiGraph, vendor_id: str) -> list[str]:
    partners = set()
    for u, v, data in g.edges(vendor_id, data=True):
        if data.get("relation") == "CO_BID_WITH":
            partners.add(v if u == vendor_id else u)
    return sorted(partners)


def vendor_relationship_summary(g: nx.MultiGraph, vendor_id: str) -> dict:
    """Everything the orchestrator's query_vendor_graph tool needs about
    one vendor's position in the relationship graph.
    """
    clusters = shell_cluster_candidates(g)
    own_cluster = next((c for c in clusters if vendor_id in c), None)

    return {
        "vendor_id": vendor_id,
        "co_bidders": co_bidding_partners(g, vendor_id),
        "shell_cluster_members": sorted(own_cluster - {vendor_id}) if own_cluster else [],
        "in_shell_cluster": own_cluster is not None,
    }


if __name__ == "__main__":
    conn = sqlite3.connect("data/argus.db")
    g = build_graph(conn)
    clusters = shell_cluster_candidates(g)
    print(f"{len(clusters)} shell/shared-attribute cluster(s) found")
    for c in clusters:
        print(sorted(c))
