create or replace function public.preserve_policyclaw2_nonempty_content()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if nullif(btrim(new.content), '') is null
     and nullif(btrim(old.content), '') is not null then
    new.content := old.content;
  end if;
  return new;
end;
$$;

drop trigger if exists preserve_policyclaw2_nonempty_content
on public.policyclaw2;

create trigger preserve_policyclaw2_nonempty_content
before update of content on public.policyclaw2
for each row
execute function public.preserve_policyclaw2_nonempty_content();
