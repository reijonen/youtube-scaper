<script lang="ts">
	import { views } from '$lib/views';
</script>

<h1>web-ui</h1>
<ul>
	{#each views as view (view.id)}
		<li><a href="/{view.id}">{view.title}</a> — {view.question}</li>
	{/each}
</ul>
