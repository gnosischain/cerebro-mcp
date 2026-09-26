-- Spam reason for an enriched row, '' when none. Registry tokens are never spam.
-- First match wins: impersonation (the ASCII fold of the symbol equals a reviewed
-- symbol on any chain, but the address is not reviewed), lure (URLs, domains,
-- call-to-action verbs, dollar amounts), obfuscated (invisible, combining or
-- homoglyph characters, after stripping emoji variation selectors), malformed
-- (overlong text), mass_airdrop (held by most of the chain's active wallets).
-- Python twin: treasury_registry.classify; parity via tests/treasury_spam_fixtures.py.
multiIf(
  x_role != '', '',
  has({vs_fold:Array(String)},
      upper(replaceRegexpAll(ifNull(x_symbol, ''), '[^A-Za-z0-9.]', ''))), 'impersonation',
  match(concat(ifNull(x_symbol, ''), ' ', ifNull(x_name, '')), {lure_re:String}), 'lure',
  match(replaceRegexpAll(concat(ifNull(x_symbol, ''), ' ', ifNull(x_name, '')),
                         {vs_re:String}, ''), {obf_re:String}), 'obfuscated',
  lengthUTF8(ifNull(x_symbol, '')) > @max_symbol_len
    OR lengthUTF8(ifNull(x_name, '')) > @max_name_len, 'malformed',
  x_active >= @mass_min_wallets AND x_wallets >= ceil(@mass_share * x_active), 'mass_airdrop',
  '')
