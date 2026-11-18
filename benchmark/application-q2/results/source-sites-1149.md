# Source sites for the 18 zero-row development cases

This pass locates a representative source change in each case named in
[#1149](https://github.com/ThreeMoonsLab/agents-shipgate/issues/1149).
The links pin the exact heads in [development.json](../development.json).
The review compared the source at each recorded merge base and head.

- **a** is a tool body or wrapper.
- **b** is a list or call argument.
- **c** is a condition, comprehension, loop or in-place edit.
- **d** is a registry, instance field, configuration or another source site.

Classes describe the location of the witnessed change. A row can carry more
than one class. Where a binding depends on another source hop, the notes name
that dependency. Coverage of every true change and independent source
adjudication remain open.

| Case | Class | Pinned source observation |
| --- | --- | --- |
| [FEDOT.MAS#30](https://github.com/ITMO-NSS-team/FEDOT.MAS/pull/30) | c, d | The new ADK backend builds tools by dispatching on descriptors, appending an MCP toolset or a wrapped function. A descriptor must resolve before a particular tool name can be claimed. The new file alone cannot distinguish a moved implementation from a new binding. [Source](https://github.com/ITMO-NSS-team/FEDOT.MAS/blob/706bc62529040ed9916c99fbff3bbd73f7c968d3/packages/fedotmas/src/fedotmas/backends/adk/builder.py#L116) |
| [FEDOT.MAS#36](https://github.com/ITMO-NSS-team/FEDOT.MAS/pull/36) | d | MCP toolset creation passes through the configured name prefix. This can change advertised names when a prefix is configured. The patch site cannot establish the names returned by a server. [Source](https://github.com/ITMO-NSS-team/FEDOT.MAS/blob/f599cfd0c73f782c3a7b2209a818b37ad7c48efb/packages/fedotmas/src/fedotmas/mcp/registry.py#L69) |
| [FEDOT.MAS#38](https://github.com/ITMO-NSS-team/FEDOT.MAS/pull/38) | b | The coordinator builder now passes workers through the constructor instead of assigning them afterward. The worker identities and effects still need source evidence. [Source](https://github.com/ITMO-NSS-team/FEDOT.MAS/blob/60a1e9645bec16616bf1500e498bcf996ce8352d/packages/fedotmas/src/fedotmas/mas/builder.py#L53) |
| [FEDOT.MAS#42](https://github.com/ITMO-NSS-team/FEDOT.MAS/pull/42) | d | MAS and MAW now prepare server descriptions from advertised tools for registry entries lacking descriptions. This verifies a registry preparation change. An agent binding change in this case remains unconfirmed. [Source](https://github.com/ITMO-NSS-team/FEDOT.MAS/blob/35f1aee6030105056efdd39ed10f01c53d20e9c8/packages/fedotmas/src/fedotmas/core/base.py#L107) |
| [GRAAL#33](https://github.com/InseeFrLab/GRAAL/pull/33) | d | CodeChooser and MatchVerifier now override the new BaseAgent.get_tools hook to return empty lists. The base previously populated the tools attribute directly from the graph. A read of the unchanged clone site alone misses both overrides. [Source](https://github.com/InseeFrLab/GRAAL/blob/f3c8737b96ded6da83b36ce6b179f2c4c692b641/src/agents/closers/code_chooser.py#L47) |
| [agr_ai_curation#842](https://github.com/alliance-genome/agr_ai_curation/pull/842) | c, d | The new Agent Studio runtime builds SDK FunctionTool objects from supplied definitions. A changed factory establishes that code path. Which tools a particular profile passes to it remains unresolved here. [Source](https://github.com/alliance-genome/agr_ai_curation/blob/d37ec5fb4b4226fc5d118e3da8ebd2aefaf89935/backend/src/lib/agent_studio/openai_runtime.py#L168) |
| [agr_ai_curation#858](https://github.com/alliance-genome/agr_ai_curation/pull/858) | c | The compact runtime replaces the agent tools list through a comprehension that wraps entries whose names are in the lookup set. Which entries match depends on the incoming agent and runtime. [Source](https://github.com/alliance-genome/agr_ai_curation/blob/48430990ab4d813f57ad9f31ff1765aff9433d8b/backend/src/lib/domain_packs/compact_runtime.py#L175) |
| [agr_ai_curation#860](https://github.com/alliance-genome/agr_ai_curation/pull/860) | c, d | The runtime returns the tools selected by the compiled surface instead of returning its locally assembled eager and deferred list. Exact tool membership requires following the profile and policy inputs to that compilation. [Source](https://github.com/alliance-genome/agr_ai_curation/blob/e1fd3ca35a9cdf8d33d5744f0e52e25bdc979161/backend/src/lib/agent_studio/openai_runtime.py#L281) |
| [pastoriza#66](https://github.com/carlosdaniel092015-crypto/pastoriza/pull/66) | a | The existing decorated cotizar entry delegates to a new implementation that records the pre-tax subtotal separately for the order minimum check. The tool body change is behind the wrapper. [Source](https://github.com/carlosdaniel092015-crypto/pastoriza/blob/d2595e7c3ea58eacff0e53e5a5aa36446e3e759c/app/tools/cotizar_tools.py#L60) |
| [pastoriza#69](https://github.com/carlosdaniel092015-crypto/pastoriza/pull/69) | a | The contact creation and update tools delegate to implementations that also retain the saved phone number in conversation context. The decorator moves to the wrapper under the existing published names. [Source](https://github.com/carlosdaniel092015-crypto/pastoriza/blob/b13886ebd37939e6a7823dde2765e730f81151e2/app/tools/odoo_tools.py#L150) |
| [starchatter-telegram#4](https://github.com/starfall-orb/starchatter-telegram/pull/4) | c, d | The runtime obtains enabled MCP servers from the database and assembles the list it passes onward. The base used a fixed server. The change in list origin is visible, while the databases and server tool membership are outside the pinned source. [Source](https://github.com/starfall-orb/starchatter-telegram/blob/0eaa9f234b1157609400508dc6e01efaffaa3540/app/ai/agent.py#L181) |
| [skye-next#17](https://github.com/evvyraine/skye-next/pull/17) | a, c | The runtime appends delivery.tool() to the list it passes to Agent. The local factory returns the decorated send_message function. Following the factory establishes the tool name. [Source](https://github.com/evvyraine/skye-next/blob/4b7ed567456d04800d4bd918096b842e25f6574b/src/skye/runtime.py#L812) |
| [llm-agent-for-ai-studio#19](https://github.com/krevetka-is-afk/llm-agent-for-ai-studio/pull/19) | d | The base class now retains the constructor's tool list on the instance and forwards it when constructing the agent. The receiver-specific lists require following subclass call sites. [Source](https://github.com/krevetka-is-afk/llm-agent-for-ai-studio/blob/cd3c9f5dcd78e57f1f67a10509df687b9d3fb656/src/custom_agents/base_agent.py#L30) |
| [llm-agent-for-ai-studio#20](https://github.com/krevetka-is-afk/llm-agent-for-ai-studio/pull/20) | c, d | The new runtime compiles tool descriptors into its native tool configuration. This is source evidence of a compilation path. The SDK agent receiving these tools, if any, remains unresolved. [Source](https://github.com/krevetka-is-afk/llm-agent-for-ai-studio/blob/1868de9be838afedf3a24f5b9dbfa308d15ea333/src/agent_runtime.py#L81) |
| [llm-agent-for-ai-studio#23](https://github.com/krevetka-is-afk/llm-agent-for-ai-studio/pull/23) | c | The one-prompt and RAG specification builders conditionally append a code-interpreter descriptor. A binding claim must carry the enablement condition and identify the runtime that consumes the descriptor. [Source](https://github.com/krevetka-is-afk/llm-agent-for-ai-studio/blob/7a422b38f933274955e18125170c6c19d87a16b0/src/ai_studio_agent_builder/domain/specification.py#L422) |
| [Vesta#53](https://github.com/mezgoodle/Vesta/pull/53) | b | The service passes the memory group to the root factory and the factory forwards it into the agent constructor. The group is optional, so a source statement must qualify when it is supplied. [Source](https://github.com/mezgoodle/Vesta/blob/e5095d3d5779f6827787086088c2cb56302f04d6/backend/app/services/adk_service.py#L141) |
| [Vesta#58](https://github.com/mezgoodle/Vesta/pull/58) | b | The secretary receives the concatenated calendar and email groups. The root sub-agent list replaces calendar with secretary. The new email group includes check_emails, alongside the calendar tools get_calendar_events and schedule_event_tool. [Source](https://github.com/mezgoodle/Vesta/blob/8626cba5bd1584eec6f804040139747deb44bf7c/backend/app/services/adk_service.py#L124) |
| [Vesta#70](https://github.com/mezgoodle/Vesta/pull/70) | b, c | The existing secretary tool argument adds the tasks group when that key is present. The source names get_tasks_tool, create_task_tool, complete_task_tool and delete_task_tool in that group. [Source](https://github.com/mezgoodle/Vesta/blob/e15e6a7bc31789297a6aac301878eb39ed927931/backend/app/services/adk_service.py#L126) |

## Prototype replay

[The changed-lines probe](../changed_lines.py) reads the changed Python
lines on both sides of each pinned comparison. It marks edited arguments
to directly imported SDK or ADK agent constructors, calls to recognized
`FunctionTool` constructors, and edited functions whose import spelling
resolves to the SDK decorator. Each marker records the first added or removed
line in the marked span. Competing import bindings are withheld. The probe
parses source with Python AST and uses the existing local Git caches.

```sh
python benchmark/application-q2/changed_lines.py \
  --corpus benchmark/application-q2/development.json \
  --clones ~/.cache/shipgate-q2/repos > changed-lines.json
```

CPython 3.12.13 read all 49 pinned comparisons. The JSON
output has SHA-256 `08a2b72f85e7f5557c0be71c0090e5fee0a5763149bede2de7deabf214f39bfc`.
The probe found markers in 30 of 49 cases and 14 of the
18 cases above. In 7 of the 18 it found a head-side marker in the
same file as the representative observation. These counts describe syntax
locations. Confirming a binding change requires tracing it from source.

| Case | Constructor arguments | Tool wrappers | Decorated functions |
| --- | ---: | ---: | ---: |
| [FEDOT.MAS#30](https://github.com/ITMO-NSS-team/FEDOT.MAS/pull/30) | 7 | 2 | 0 |
| [FEDOT.MAS#36](https://github.com/ITMO-NSS-team/FEDOT.MAS/pull/36) | 0 | 0 | 0 |
| [FEDOT.MAS#38](https://github.com/ITMO-NSS-team/FEDOT.MAS/pull/38) | 2 | 0 | 0 |
| [FEDOT.MAS#42](https://github.com/ITMO-NSS-team/FEDOT.MAS/pull/42) | 0 | 0 | 0 |
| [GRAAL#33](https://github.com/InseeFrLab/GRAAL/pull/33) | 0 | 0 | 2 |
| [voice-freight-negotiator#10](https://github.com/JSebastianIEU/voice-freight-negotiator/pull/10) | 0 | 0 | 0 |
| [Agentic-AI-Scientist#2](https://github.com/JinyanSu1/Agentic-AI-Scientist/pull/2) | 1 | 0 | 3 |
| [OpenCMO#41](https://github.com/Lling0000/OpenCMO/pull/41) | 0 | 0 | 0 |
| [Sponsio#173](https://github.com/SponsioLabs/Sponsio/pull/173) | 0 | 0 | 0 |
| [MIS_TALENT#5](https://github.com/Tiendat2703/MIS_TALENT/pull/5) | 0 | 0 | 1 |
| [MIS_TALENT#6](https://github.com/Tiendat2703/MIS_TALENT/pull/6) | 0 | 0 | 0 |
| [MIS_TALENT#7](https://github.com/Tiendat2703/MIS_TALENT/pull/7) | 0 | 0 | 3 |
| [MIS_TALENT#8](https://github.com/Tiendat2703/MIS_TALENT/pull/8) | 2 | 0 | 10 |
| [agency-swarm#796](https://github.com/VRSEN/agency-swarm/pull/796) | 0 | 0 | 0 |
| [agency-swarm#804](https://github.com/VRSEN/agency-swarm/pull/804) | 0 | 1 | 0 |
| [O.R.I.O.N#126](https://github.com/VidulaWickramasinghe/O.R.I.O.N/pull/126) | 0 | 0 | 77 |
| [internstore-migrate#4](https://github.com/YaroslavMarkivskyi/internstore-migrate/pull/4) | 0 | 0 | 0 |
| [zeroruntime-python-examples#1](https://github.com/ZeroRuntimeAI/zeroruntime-python-examples/pull/1) | 0 | 0 | 0 |
| [zeroruntime-python-examples#5](https://github.com/ZeroRuntimeAI/zeroruntime-python-examples/pull/5) | 0 | 0 | 0 |
| [agr_ai_curation#842](https://github.com/alliance-genome/agr_ai_curation/pull/842) | 3 | 1 | 16 |
| [agr_ai_curation#858](https://github.com/alliance-genome/agr_ai_curation/pull/858) | 0 | 0 | 1 |
| [agr_ai_curation#860](https://github.com/alliance-genome/agr_ai_curation/pull/860) | 23 | 5 | 58 |
| [cinescout-ai#9](https://github.com/buriro-ezekia/cinescout-ai/pull/9) | 2 | 0 | 0 |
| [pastoriza#66](https://github.com/carlosdaniel092015-crypto/pastoriza/pull/66) | 0 | 0 | 2 |
| [pastoriza#69](https://github.com/carlosdaniel092015-crypto/pastoriza/pull/69) | 0 | 0 | 4 |
| [chack-agent#25](https://github.com/carlospolop/chack-agent/pull/25) | 0 | 0 | 0 |
| [tuatha#1](https://github.com/cianfhoghlaim/tuatha/pull/1) | 0 | 80 | 0 |
| [starchatter-telegram#4](https://github.com/starfall-orb/starchatter-telegram/pull/4) | 0 | 0 | 0 |
| [adk_tutorial#7](https://github.com/cuppibla/adk_tutorial/pull/7) | 12 | 0 | 0 |
| [adk_tutorial#9](https://github.com/cuppibla/adk_tutorial/pull/9) | 2 | 0 | 0 |
| [skye-next#17](https://github.com/evvyraine/skye-next/pull/17) | 0 | 0 | 1 |
| [livekit-agents#1](https://github.com/geroembser/livekit-agents/pull/1) | 0 | 0 | 0 |
| [multi-agent-course#63](https://github.com/hamzafarooq/multi-agent-course/pull/63) | 0 | 0 | 0 |
| [multi-agent-course#64](https://github.com/hamzafarooq/multi-agent-course/pull/64) | 0 | 0 | 0 |
| [attest#3](https://github.com/jpka/attest/pull/3) | 2 | 0 | 0 |
| [CRM#5](https://github.com/kkmiecik-coder/CRM/pull/5) | 0 | 0 | 7 |
| [llm-agent-for-ai-studio#19](https://github.com/krevetka-is-afk/llm-agent-for-ai-studio/pull/19) | 2 | 0 | 14 |
| [llm-agent-for-ai-studio#20](https://github.com/krevetka-is-afk/llm-agent-for-ai-studio/pull/20) | 0 | 0 | 2 |
| [llm-agent-for-ai-studio#23](https://github.com/krevetka-is-afk/llm-agent-for-ai-studio/pull/23) | 2 | 0 | 12 |
| [siada-cli#6](https://github.com/liauto-siada/siada-cli/pull/6) | 13 | 2 | 22 |
| [agent-desk#8](https://github.com/lifenyan/agent-desk/pull/8) | 0 | 0 | 0 |
| [Vesta#53](https://github.com/mezgoodle/Vesta/pull/53) | 1 | 0 | 0 |
| [Vesta#58](https://github.com/mezgoodle/Vesta/pull/58) | 2 | 0 | 0 |
| [Vesta#70](https://github.com/mezgoodle/Vesta/pull/70) | 0 | 0 | 0 |
| [realestatecrm#612](https://github.com/mosnin/realestatecrm/pull/612) | 0 | 0 | 0 |
| [rustic-ai#419](https://github.com/rustic-ai/rustic-ai/pull/419) | 0 | 0 | 0 |
| [agent-memory-openai-agents-sdk#1](https://github.com/surrealdb/agent-memory-openai-agents-sdk/pull/1) | 8 | 0 | 10 |
| [hepagent#72](https://github.com/xju2/hepagent/pull/72) | 1 | 0 | 6 |
| [hepagent#74](https://github.com/xju2/hepagent/pull/74) | 0 | 0 | 17 |
| **Total** | **85** | **91** | **268** |

The complete source adjudication, hand scoring and decision under #1149's
proposed rule are pending.
